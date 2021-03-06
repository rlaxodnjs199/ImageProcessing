import os
import argparse
import pathlib
from typing import Dict
from pydicom import dcmread
from tqdm import tqdm
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("src", metavar="src", type=pathlib.Path, help="DICOM folder path")

args = parser.parse_args()
src_path = args.src

num_files = 0
for base, _, files in os.walk(src_path):
    num_files += len(files)

last_state = 0
dcm_dict: Dict = {}
for base, _, files in os.walk(src_path):
    for file in tqdm(files, total=num_files, initial=last_state):
        folder_name = os.path.basename(base)
        subj = folder_name.split("_")[0]
        ctdate = folder_name.split("_")[1]
        file_path = os.path.join(base, file)
        dcm = dcmread(file_path)
        if hasattr(dcm, "SliceThickness"):
            slice_thickness = dcm.SliceThickness
        else:
            slice_thickness = "None"
        series_description = dcm.SeriesDescription

        if folder_name in dcm_dict:
            if series_description not in dcm_dict[folder_name]:
                dcm_dict[folder_name].append(slice_thickness)
                dcm_dict[folder_name].append(series_description)
                dcm_dict[folder_name].append(1)
            else:
                count_index = dcm_dict[folder_name].index(series_description) + 1
                try:
                    dcm_dict[folder_name][count_index] += 1
                except:
                    print(series_description, dcm_dict[folder_name][count_index])
        else:
            dcm_dict[folder_name] = [slice_thickness, series_description, 1]

    last_state += len(files)

df_dcm_dict = pd.DataFrame.from_dict(dcm_dict, orient="index")
print(df_dcm_dict)
df_dcm_dict.to_csv("./output/series_descriptions_v2.csv", header=False)
