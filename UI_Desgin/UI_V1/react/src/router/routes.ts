import Frame2783 from "@/views/Frame2783";
import Frame31 from "@/views/Frame31";
import Frame21180 from "@/views/Frame21180";
import Frame2422 from "@/views/Frame2422";
import Frame21536 from "@/views/Frame21536";
import Frame71 from "@/views/Frame71";
import Frame22512 from "@/views/Frame22512";
import Frame22042 from "@/views/Frame22042";
import Frame2115 from "@/views/Frame2115";
import Frame21839 from "@/views/Frame21839";
import Frame31240 from "@/views/Frame31240";
import Frame21 from "@/views/Frame21";
import Frame3486 from "@/views/Frame3486";
import Frame3898 from "@/views/Frame3898";

export const routes = [{
          path: "/frame2783",
          component: Frame2783,
          guid: "2:783",
        },
{
          path: "/frame31",
          component: Frame31,
          guid: "3:1",
        },
{
          path: "/frame21180",
          component: Frame21180,
          guid: "2:1180",
        },
{
          path: "/frame2422",
          component: Frame2422,
          guid: "2:422",
        },
{
          path: "/frame21536",
          component: Frame21536,
          guid: "2:1536",
        },
{
          path: "/frame71",
          component: Frame71,
          guid: "7:1",
        },
{
          path: "/frame22512",
          component: Frame22512,
          guid: "2:2512",
        },
{
          path: "/frame22042",
          component: Frame22042,
          guid: "2:2042",
        },
{
          path: "/frame2115",
          component: Frame2115,
          guid: "2:115",
        },
{
          path: "/frame21839",
          component: Frame21839,
          guid: "2:1839",
        },
{
          path: "/frame31240",
          component: Frame31240,
          guid: "3:1240",
        },
{
          path: "/",
          component: Frame21,
          guid: "2:1",
        },
{
          path: "/frame3486",
          component: Frame3486,
          guid: "3:486",
        },
{
          path: "/frame3898",
          component: Frame3898,
          guid: "3:898",
        }];


export const guidPathMap = new Map(
  routes.map((item) => [item.guid, item.path])
);
export const pathGuidMap = new Map(
  routes.map((item) => [item.path, item.guid])
);

export const getPathByGuid = (guid: string) => {
  return guidPathMap.get(guid) || "";
};

export const getGuidByPath = (path: string) => {
  return pathGuidMap.get(path) || "";
};
