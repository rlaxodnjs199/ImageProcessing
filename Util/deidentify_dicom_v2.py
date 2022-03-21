import os
import string
import pathlib
from os.path import basename, dirname
import argparse
from typing import List, Tuple, Union
from tqdm import tqdm
from pydicom import Dataset, dcmread
from loguru import logger
import gspread
from dotenv import dotenv_values

# Create an argument parser ##############################################################
parser = argparse.ArgumentParser(
    description="Recursively de-identify and save DICOM images in the DICOM source folder"
)
parser.add_argument("src", metavar="src", type=pathlib.Path, help="DICOM folder path")
parser.add_argument("-p", "--project", action="store", type=str, help="Project Name")
parser.add_argument("-s", "--subject", action="store", type=str, help="Subject ID")
parser.add_argument("-d", "--date", action="store", type=str, help="CT Date")
parser.add_argument("-fu", "--followup", action="store", type=str, help="Follow Up")
parser.add_argument("-t", "--type", action="store", type=str, help="IN or EX")

args = parser.parse_args()
src_dcm_dir = args.src

# Global variables ########################################################################
TAGS_TO_ANONYMIZE = [
    # 'PatientBirthDate',
    # 'PatientSex',
    # 'PatientAge',
    "InstitutionName",
    "InstitutionAddress",
    "InstitutionalDepartmentName",
    "ReferringPhysicianName",
    "ReferringPhysicianTelephoneNumbers",
    "ReferringPhysicianAddress",
    "PhysiciansOfRecord",
    "OperatorsName",
    "IssuerOfPatientID",
    "OtherPatientIDs",
    "OtherPatientNames",
    "OtherPatientIDsSequence",
    "PatientBirthName",
    # 'PatientSize',
    # 'PatientWeight',
    "PatientAddress",
    "PatientMotherBirthName",
    "CountryOfResidence",
    "RegionOfResidence",
    "CurrentPatientLocation",
    "PatientTelephoneNumbers",
    "SmokingStatus",
    "PregnancyStatus",
    "PatientReligiousPreference",
    "RequestingPhysician",
    "PerformingPhysicianName",
    "NameOfPhysiciansReadingStudy",
    "MilitaryRank",
    "EthnicGroup",
    "AdditionalPatientHistory",
    "PatientComments",
    "PersonName",
    "ScheduledPatientInstitutionResidence",
]
VALIDATE_SUBSTRINGS = {"IN": ["IN", "TLC"], "EX": ["EX", "RV"]}
PROCESSED_SERIES_DESCRIPTION = {"IN": {}, "EX": {}}
IN = 0
EX = 0
########################################################################################

# GoogleSheet variables ################################################################
CONFIG = dotenv_values(
    r"C:\Users\tkim3\Documents\Codes\ImageProcessing\Scripts\Util\.env"
)
GOOGLE_SA = gspread.service_account(filename=CONFIG["GOOGLE_TOKEN_PATH"])
QCTWORKSHEET = GOOGLE_SA.open_by_key(CONFIG["QCTWORKSHEET_GOOGLE_API_KEY"])
########################################################################################


def get_metadata_from_dcm_path(dcm_dir: str) -> Tuple[str]:
    if args.project:
        proj = args.project
    else:
        proj = basename(dirname(dcm_dir)).split("_")[-2]

    if args.subject:
        subj = args.subject
    else:
        subj = basename(dcm_dir).split("_")[0]

    if args.date:
        ctdate = args.date
    else:
        ctdate = basename(dcm_dir).split("_")[1]

    return proj, subj, ctdate


def get_dcm_paths_from_dcm_dir(src_dcm_dir: str) -> List[pathlib.Path]:
    dcm_paths = []
    for base, _, files in os.walk(src_dcm_dir):
        for file in files:
            full_file_path = os.path.join(base, file)
            if str(dirname(full_file_path)) == str(src_dcm_dir):
                dcm_paths.append(full_file_path)
            else:
                logger.error(
                    "DCM file depth greater than 1. Check the original DCM folder."
                )
                return []
    return dcm_paths


def prepare_deid_dcm_dir(src_dcm_dir) -> pathlib.Path:
    dcm_dir_root = dirname(src_dcm_dir)
    deid_dcm_dir_components = basename(dcm_dir_root).split("_")[:3]
    deid_dcm_dir_components.extend(["deid", basename(dcm_dir_root).split("_")[-1]])
    deid_dcm_dir = ("_").join(deid_dcm_dir_components)
    deid_dcm_dir_path = os.path.join(dirname(dcm_dir_root), deid_dcm_dir)
    deid_dcm_dir_child_path = os.path.join(deid_dcm_dir_path, basename(src_dcm_dir))

    if not os.path.exists(deid_dcm_dir_path):
        os.mkdir(deid_dcm_dir_path)

    if not os.path.exists(deid_dcm_dir_child_path):
        os.mkdir(deid_dcm_dir_child_path)

    return deid_dcm_dir_child_path


def filter_dcm_img_to_deidentify(dcm_img: Dataset) -> Union[str, bool]:
    global IN, EX
    for VALIDATE_SUBSTRING in VALIDATE_SUBSTRINGS["IN"]:
        if VALIDATE_SUBSTRING in dcm_img.SeriesDescription.upper():
            IN = 1
            return "IN"
    for VALIDATE_SUBSTRING in VALIDATE_SUBSTRINGS["EX"]:
        if VALIDATE_SUBSTRING in dcm_img.SeriesDescription.upper():
            EX = 1
            return "EX"
    return False


def update_QCTWorksheet(proj: str, subj: str, ctdate: str, fu: str):
    global IN, EX
    cell_lookup_list = QCTWORKSHEET.worksheet(proj).findall(subj)
    for cell_lookup in cell_lookup_list:
        cell_lookup_ctdate = QCTWORKSHEET.worksheet(proj).row_values(cell_lookup.row)[2]
        if ctdate == cell_lookup_ctdate:
            QCTWORKSHEET.worksheet(proj).update_cell(cell_lookup.row, 4, str(fu))
            if IN:
                QCTWORKSHEET.worksheet(proj).update_cell(cell_lookup.row, 5, "O")
            if EX:
                QCTWORKSHEET.worksheet(proj).update_cell(cell_lookup.row, 6, "O")
            logger.info(
                f"QCTWorksheet row updated: {proj}-{subj}-{ctdate}: {QCTWORKSHEET.worksheet(proj).row_values(cell_lookup.row)}"
            )


def calculate_fu(proj: str, subj: str) -> int:
    if args.followup:
        fu = args.followup
    else:
        subj_lookup_list = QCTWORKSHEET.worksheet(proj).findall(subj)
        fu = len(subj_lookup_list) - 1
        if fu < 0:
            logger.error("Entry did not exist in QCTWorksheet")
            return 0
    return fu


@logger.catch
def deidentify_dcm_img(
    dcm_img_path: pathlib.Path,
    deid_dcm_dir: pathlib.Path,
    proj: str,
    subj: str,
    fu: int,
):
    def construct_deid_dcm_img_dir_path(
        deid_dcm_dir, proj, subj, in_or_ex, fu, dcm_img
    ) -> str:
        series_uuid = dcm_img.SeriesInstanceUID
        if series_uuid in PROCESSED_SERIES_DESCRIPTION[in_or_ex]:
            return PROCESSED_SERIES_DESCRIPTION[in_or_ex][series_uuid]
        else:
            deid_dcm_img_dir_components = [
                "DCM",
                proj,
                subj.replace("-", ""),
                in_or_ex + str(fu),
            ]
            deid_dcm_img_dirname = ("_").join(deid_dcm_img_dir_components)
            if len(PROCESSED_SERIES_DESCRIPTION[in_or_ex]) == 0:
                deid_dcm_img_dir_path = os.path.join(deid_dcm_dir, deid_dcm_img_dirname)
                PROCESSED_SERIES_DESCRIPTION[in_or_ex][series_uuid] = os.path.join(
                    deid_dcm_dir, deid_dcm_img_dirname
                )

                if not os.path.exists(deid_dcm_img_dir_path):
                    os.mkdir(deid_dcm_img_dir_path)

                return deid_dcm_img_dir_path
            else:
                alphabet_list = list(string.ascii_lowercase)
                alphabet = alphabet_list[
                    len(PROCESSED_SERIES_DESCRIPTION[in_or_ex]) - 1
                ]
                deid_dcm_img_dirname = deid_dcm_img_dirname + alphabet
                deid_dcm_img_dir_path = os.path.join(deid_dcm_dir, deid_dcm_img_dirname)
                PROCESSED_SERIES_DESCRIPTION[in_or_ex][series_uuid] = os.path.join(
                    deid_dcm_dir, deid_dcm_img_dirname
                )

                if not os.path.exists(deid_dcm_img_dir_path):
                    os.mkdir(deid_dcm_img_dir_path)

                return deid_dcm_img_dir_path

    dcm_img: Dataset = dcmread(dcm_img_path)
    in_or_ex = args.type if args.type else filter_dcm_img_to_deidentify(dcm_img)

    if in_or_ex:
        # PatientID = subj, PatientName = subj
        try:
            dcm_img.PatientID = dcm_img.PatientName = subj
        except:
            logger.warning(
                f"{dcm_img_path}: [PatientID] or [PatientName] tag does not exist"
            )
        # Anonymize month & day of birthdate to '0101'
        try:
            dcm_img.PatientBirthDate = dcm_img.PatientBirthDate[:-4] + "0101"
        except:
            logger.warning(f"{dcm_img_path}: [PatientBirthDate] tag does not exist")
        # Delete tags to anonymize
        for tag in TAGS_TO_ANONYMIZE:
            if tag in dcm_img:
                delattr(dcm_img, tag)
        dcm_img.remove_private_tags()
        # Record proj at 'DeidentificationMethod' tag
        dcm_img.add_new([0x0012, 0x0063], "LO", proj)

        # Save deidentified DICOM img
        deid_dcm_img_dir_path = construct_deid_dcm_img_dir_path(
            deid_dcm_dir, proj, subj, in_or_ex, fu, dcm_img
        )
        deid_dcm_img_path = os.path.join(deid_dcm_img_dir_path, basename(dcm_img_path))
        dcm_img.save_as(deid_dcm_img_path)


if __name__ == "__main__":
    logger.info(f"De-Identification started: {src_dcm_dir}")

    proj, subj, ctdate = get_metadata_from_dcm_path(src_dcm_dir)
    dcm_img_paths = get_dcm_paths_from_dcm_dir(src_dcm_dir)
    deid_dcm_dir = prepare_deid_dcm_dir(src_dcm_dir)
    fu = calculate_fu(proj, subj)

    for dcm_img_path in tqdm(dcm_img_paths):
        deidentify_dcm_img(dcm_img_path, deid_dcm_dir, proj, subj, fu)

    logger.info(f"De-Identification finished: Results at {deid_dcm_dir}")

    update_QCTWorksheet(proj, subj, ctdate, fu)
