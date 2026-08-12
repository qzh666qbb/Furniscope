"""Development/test JSON market import worker using explicitly synthetic or authorized input."""

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from .demo_storage import DemoStorage
from .idempotency_service import IdempotencyService


class DatasetImportService:
    demo_only = True

    def __init__(self, settings: ApiSettings) -> None:
        self.storage=DemoStorage(settings)
        self.idempotency=IdempotencyService()

    async def accept(self,session:AsyncSession,*,tenant_id:int,user_id:int,dataset_id:int,
                     idempotency_key:str,deduplication_strategy:str,filename:str,mime:str,content:bytes,
                     response_envelope) -> tuple[int,dict[str,Any]]:
        if deduplication_strategy != "platform_id_latest":
            raise BusinessError("FIELD_MAPPING_INVALID","P0去重策略仅支持platform_id_latest",status_code=422)
        dataset=(await session.execute(text("""
            SELECT id,status FROM market_datasets WHERE id=:id AND tenant_id=:tenant AND deleted_at IS NULL FOR UPDATE
        """),{"id":dataset_id,"tenant":tenant_id})).mappings().one_or_none()
        if dataset is None: raise BusinessError("DATASET_NOT_FOUND","数据集不存在或不可访问",status_code=404)
        if dataset["status"] == "validating": raise BusinessError("DATASET_IMPORT_IN_PROGRESS","数据集正在导入",status_code=409)
        if mime != "application/json": raise BusinessError("DATASET_FILE_INVALID","P0导入文件必须为JSON",status_code=422)
        try:
            parsed=json.loads(content)
            if not isinstance(parsed,dict) or not isinstance(parsed.get("listings"),list) or not isinstance(parsed.get("reviews"),list):
                raise ValueError
        except (UnicodeDecodeError,json.JSONDecodeError,ValueError) as exc:
            raise BusinessError("DATASET_FILE_INVALID","JSON必须包含listings和reviews数组",status_code=422) from exc
        decision=await self.idempotency.begin(session,tenant_id=tenant_id,actor_user_id=user_id,
            route_code="API-DAT-02",http_method="POST",idempotency_key=idempotency_key,
            request_payload={"dataset_id":dataset_id,"deduplication_strategy":deduplication_strategy,
                             "file":{"name":filename,"mime":mime,"sha256":hashlib.sha256(content).hexdigest()}})
        if decision.action=="replay": return int(decision.response_status),dict(decision.response_body or {})
        key,digest=self.storage.store(tenant_id=tenant_id,filename=filename,mime_type=mime,content=content)
        asset_id=int((await session.execute(text("""
            INSERT INTO file_assets(tenant_id,owner_type,owner_id,asset_type,original_filename,storage_key,mime_type,
              size_bytes,sha256,security_status,parse_status,created_by)
            VALUES(:tenant,'dataset',:dataset,'json',:name,:key,:mime,:size,:sha,'clean','processing',:user) RETURNING id
        """),{"tenant":tenant_id,"dataset":dataset_id,"name":filename,"key":key,"mime":mime,
               "size":len(content),"sha":digest,"user":user_id})).scalar_one())
        accepted_at=datetime.now(timezone.utc)
        await session.execute(text("""
            UPDATE market_datasets SET import_asset_id=:asset,status='validating' WHERE id=:id AND tenant_id=:tenant
        """),{"asset":asset_id,"id":dataset_id,"tenant":tenant_id})
        envelope=response_envelope({"dataset_id":dataset_id,"status":"validating","accepted_at":accepted_at})
        await self.idempotency.finish(session,tenant_id=tenant_id,record_id=decision.record_id,response_status=202,
            response_body=envelope,resource_type="market_datasets",resource_public_id=str(dataset_id))
        return 202,envelope

    async def run_demo(self,session:AsyncSession,*,tenant_id:int,dataset_id:int,content:bytes) -> None:
        payload=json.loads(content); listing_ids={}
        for item in payload["listings"]:
            required={"platform_listing_id","title","currency","sale_price","captured_at"}
            if not required.issubset(item): raise ValueError("listing required fields missing")
            try:
                captured_at=datetime.fromisoformat(str(item["captured_at"]).replace("Z","+00:00"))
            except (TypeError,ValueError) as exc:
                raise ValueError("listing captured_at invalid") from exc
            row=(await session.execute(text("""
                INSERT INTO market_listings(tenant_id,dataset_id,platform_listing_id,title,category_code,currency,
                  sale_price,captured_at,normalized_attributes,raw_payload)
                VALUES(:tenant,:dataset,:platform_id,:title,'sofa',:currency,:price,:captured,
                       CAST(:attrs AS jsonb),CAST(:raw AS jsonb))
                ON CONFLICT(dataset_id,platform_listing_id,captured_at) DO UPDATE SET title=excluded.title,sale_price=excluded.sale_price
                RETURNING id
            """),{"tenant":tenant_id,"dataset":dataset_id,"platform_id":str(item["platform_listing_id"]),
                   "title":str(item["title"]),"currency":str(item["currency"]),"price":item["sale_price"],
                   "captured":captured_at,"attrs":json.dumps(item.get("normalized_attributes",{})),
                   "raw":json.dumps(item,ensure_ascii=False)})).scalar_one()
            listing_ids[str(item["platform_listing_id"])]=int(row)
        valid=0
        for item in payload["reviews"]:
            listing_id=listing_ids.get(str(item.get("platform_listing_id")))
            if not listing_id or not item.get("platform_review_id") or not item.get("content_original"): continue
            content_original=str(item["content_original"])
            await session.execute(text("""
                INSERT INTO reviews(tenant_id,dataset_id,listing_id,platform_review_id,rating,content_original,
                  language_code,verified_purchase,content_hash)
                VALUES(:tenant,:dataset,:listing,:review_id,:rating,:content,:language,2,:hash)
                ON CONFLICT(dataset_id,platform_review_id) DO NOTHING
            """),{"tenant":tenant_id,"dataset":dataset_id,"listing":listing_id,"review_id":str(item["platform_review_id"]),
                   "rating":item.get("rating"),"content":content_original,"language":item.get("language_code","en"),
                   "hash":hashlib.sha256(content_original.encode()).hexdigest()})
            valid+=1
        await session.execute(text("""
            UPDATE market_datasets SET status='ready',listing_count=:listings,review_count=:reviews,
              valid_review_count=:valid,quality_score=CASE WHEN :listings>0 THEN 100 ELSE 0 END,
              quality_report=jsonb_build_object('source','demo_or_authorized_json','schema_valid',true),limitations='[]'::jsonb
             WHERE id=:dataset AND tenant_id=:tenant;
        """),{"listings":len(listing_ids),"reviews":len(payload["reviews"]),"valid":valid,"dataset":dataset_id,"tenant":tenant_id})
        await session.execute(text("UPDATE file_assets SET parse_status='succeeded' WHERE tenant_id=:tenant AND owner_type='dataset' AND owner_id=:dataset"),{"tenant":tenant_id,"dataset":dataset_id})
