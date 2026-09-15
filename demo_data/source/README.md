# 演示源材料

本目录保存生成 FurniScope 演示数据所需、但不进入 Git 的授权源材料。

- `HF catalog.pdf`：产品目录源文件；由 `.gitignore` 排除，不能随公开仓库分发。
- 商品图片提取脚本：`scripts/extract_hf_product_images.py`
- 授权市场演示包生成脚本：`scripts/generate_hf_demo_market.py`

脚本默认从仓库根目录按 `demo_data/source/HF catalog.pdf` 读取目录。如需使用其他文件，可通过 `--catalog` 显式指定。
