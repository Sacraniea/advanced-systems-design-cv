## CAUTION
# the code that Roboflow supplied me with:
# !pip install roboflow
import os
from pathlib import Path

current_file = Path(__file__).resolve()
local_dataset_path = current_file.parent.parent / "data" / "v2_EGH455-3"
if os.path.exists(local_dataset_path):
    print("Dataset already downloaded!")
else:
    print(f"Downloading dataset to {local_dataset_path}")
    from roboflow import Roboflow
    rf = Roboflow(api_key=os.environ["ROBOFLOW_API_KEY"])
    project = rf.workspace("bio-aidan-gmail-com").project("v2_egh455")
    version = project.version(3)
    dataset = version.download("yolo26")
    DATA_YAML = f"{dataset.location}/data.yaml"


