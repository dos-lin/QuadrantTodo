"""测试隔离：把默认数据目录指向临时目录，避免污染 exe/项目目录。

config._resolve_data_dir 优先读取 QUADRANT_DATA_DIR 环境变量，conftest 在导入
任何 quadrant_todo 模块之前设置它，因此所有测试使用的都是临时数据位置。
"""
import os
import tempfile

os.environ.setdefault("QUADRANT_DATA_DIR", tempfile.mkdtemp(prefix="qtodo_test_"))
