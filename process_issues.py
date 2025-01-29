#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright 2022 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# Permission to use, copy, modify, and/or distribute this software for any purpose
# with or without fee is hereby granted, provided that the above copyright notice
# and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED 'AS IS' AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
# REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
# FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT,
# OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE,
# DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS
# ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS
# SOFTWARE.


import csv
import io
import json
import os
import re
import time
import shutil
from datetime import datetime
from typing import List, Optional, Tuple
import logging

import requests
from oc_ds_converter.oc_idmanager.base import IdentifierManager
from oc_ds_converter.oc_idmanager.doi import DOIManager
from oc_ds_converter.oc_idmanager.isbn import ISBNManager
from oc_ds_converter.oc_idmanager.openalex import OpenAlexManager
from oc_ds_converter.oc_idmanager.pmcid import PMCIDManager
from oc_ds_converter.oc_idmanager.pmid import PMIDManager
from oc_ds_converter.oc_idmanager.url import URLManager
from oc_ds_converter.oc_idmanager.wikidata import WikidataManager
from oc_ds_converter.oc_idmanager.wikipedia import WikipediaManager
from oc_validator.main import ClosureValidator


def setup_logging():
    """Configure logging to output to console only."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],  # Solo output su console
    )


def _validate_title(title: str) -> Tuple[bool, str]:
    """Validate the format and identifier in an issue title."""
    basic_format = re.search(
        r"deposit\s+(.+?)\s+[a-zA-Z]+:.+",
        title,
        re.IGNORECASE,
    )
    if not basic_format:
        return (
            False,
            'The title of the issue was not structured correctly. Please, follow this format: deposit {domain name of journal} {doi or other supported identifier}. For example "deposit localhost:330 doi:10.1007/978-3-030-00668-6_8". The following identifiers are currently supported: doi, isbn, pmid, pmcid, url, wikidata, wikipedia, and openalex',
        )

    match = re.search(
        r"deposit\s+(.+?)\s+([a-zA-Z]+):(.+)",
        title,
        re.IGNORECASE,
    )

    identifier_schema = match.group(2).lower()
    identifier = match.group(3)

    # Map of identifier types to their manager classes
    manager_map = {
        "doi": DOIManager,
        "isbn": ISBNManager,
        "pmid": PMIDManager,
        "pmcid": PMCIDManager,
        "url": URLManager,
        "wikidata": WikidataManager,
        "wikipedia": WikipediaManager,
        "openalex": OpenAlexManager,
    }

    manager_class = manager_map.get(identifier_schema)
    if not manager_class:
        return False, f"The identifier schema '{identifier_schema}' is not supported"

    # Use API service for all identifiers that require online validation
    needs_api = {"doi", "pmid", "pmcid", "url", "wikidata", "wikipedia", "openalex"}
    id_manager: IdentifierManager = (
        manager_class(use_api_service=True)
        if identifier_schema in needs_api
        else manager_class()
    )
    is_valid = id_manager.is_valid(identifier)

    if not is_valid:
        return (
            False,
            f"The identifier with literal value {identifier} specified in the issue title is not a valid {identifier_schema.upper()}",
        )
    return True, ""


def validate(issue_title: str, issue_body: str) -> Tuple[bool, str]:
    """Validate issue title and body content using oc_validator.

    Args:
        issue_title: Title of the GitHub issue
        issue_body: Body content of the GitHub issue

    Returns:
        Tuple containing:
        - bool: Whether the content is valid
        - str: Validation message or error details
    """
    logger = logging.getLogger(__name__)

    logger.info("Starting validation")
    logger.info(f"Validating title: {issue_title}")

    # First validate the title format
    is_valid_title, title_message = _validate_title(issue_title)
    if not is_valid_title:
        logger.warning(f"Invalid title format: {title_message}")
        return False, title_message

    # Check for required separator
    if "===###===@@@===" not in issue_body:
        logger.warning("Missing required separator in issue body")
        return (
            False,
            'Please use the separator "===###===@@@===" to divide metadata from citations, as shown in the following guide: https://github.com/opencitations/crowdsourcing/blob/main/README.md',
        )

    try:
        logger.info("Creating validation output directory")
        os.makedirs("validation_output", exist_ok=True)

        # Split the data into metadata and citations
        split_data = issue_body.split("===###===@@@===")
        metadata_csv = split_data[0].strip()
        citations_csv = split_data[1].strip()

        # Create temporary files for validation
        with open("temp_metadata.csv", "w", encoding="utf-8") as f:
            f.write(metadata_csv)
        with open("temp_citations.csv", "w", encoding="utf-8") as f:
            f.write(citations_csv)

        # Initialize and run validator
        validator = ClosureValidator(
            meta_csv_doc="temp_metadata.csv",
            meta_output_dir="validation_output",
            cits_csv_doc="temp_citations.csv",
            cits_output_dir="validation_output",
            strict_sequenciality=True,
            meta_kwargs={"verify_id_existence": True},
            cits_kwargs={"verify_id_existence": True},
        )

        validator.validate()

        # Read validation results from files
        error_messages = []

        # Check metadata validation results
        meta_summary_path = os.path.join(
            "validation_output", "meta_validation_summary.txt"
        )
        if os.path.exists(meta_summary_path):
            with open(meta_summary_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:  # Only add if there are actual errors
                    error_messages.append("Metadata validation errors:")
                    error_messages.append(content)

        # Check citations validation results
        cits_summary_path = os.path.join(
            "validation_output", "cits_validation_summary.txt"
        )
        if os.path.exists(cits_summary_path):
            with open(cits_summary_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:  # Only add if there are actual errors
                    if (
                        error_messages
                    ):  # Add blank line between metadata and citation errors
                        error_messages.append("")
                    error_messages.append("Citations validation errors:")
                    error_messages.append(content)

        # Clean up all temporary files and directory
        cleanup_files = [
            "temp_metadata.csv",
            "temp_citations.csv",
        ]
        for file in cleanup_files:
            if os.path.exists(file):
                os.remove(file)

        # Remove validation_output directory if it exists
        if os.path.exists("validation_output"):
            shutil.rmtree("validation_output")

        if error_messages:
            return False, "\n".join(error_messages)

        return (
            True,
            "Thank you for your contribution! OpenCitations just processed the data you provided. The citations will soon be available on the [CROCI](https://opencitations.net/index/croci) index and metadata on OpenCitations Meta",
        )

    except Exception as e:
        logger.error(f"Validation error: {e}", exc_info=True)
        # Clean up temporary files and directory in case of error
        cleanup_files = [
            "temp_metadata.csv",
            "temp_citations.csv",
        ]
        for file in cleanup_files:
            if os.path.exists(file):
                os.remove(file)

        if os.path.exists("validation_output"):
            shutil.rmtree("validation_output")

        return (
            False,
            f"Error validating data: {str(e)}. Please ensure both metadata and citations are valid CSVs following the required format.",
        )


def answer(
    is_valid: bool, message: str, issue_number: str, is_authorized: bool = True
) -> None:
    """Update issue status and add comment using GitHub REST API.

    Args:
        is_valid: Whether the issue content is valid
        message: Comment message to add
        issue_number: GitHub issue number to update
        is_authorized: Whether the user is authorized (in safe list)
    """
    # Determine label based on validation and authorization
    if not is_authorized:
        label = "rejected"
    elif not is_valid:
        label = "invalid"
    else:
        label = "to be processed"

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    base_url = "https://api.github.com/repos/opencitations/crowdsourcing/issues"

    # Add label
    try:
        requests.post(
            f"{base_url}/{issue_number}/labels",
            headers=headers,
            json={"labels": [label]},
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"Error adding label to issue {issue_number}: {e}")
        raise

    # Add comment and close issue
    try:
        requests.post(
            f"{base_url}/{issue_number}/comments",
            headers=headers,
            json={"body": message},
            timeout=30,
        )

        requests.patch(
            f"{base_url}/{issue_number}",
            headers=headers,
            json={"state": "closed"},
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"Error closing issue {issue_number}: {e}")
        raise


def get_user_id(username: str) -> Optional[int]:
    """Get GitHub user ID from username with retries on failure.

    Args:
        username: GitHub username to lookup

    Returns:
        The user's GitHub ID if found, None otherwise
    """
    MAX_RETRIES = 3
    RETRY_DELAY = 5  # seconds

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(
                f"https://api.github.com/users/{username}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                },
                timeout=30,
            )
            if response.status_code == 200:
                return response.json().get("id")
            elif response.status_code == 404:
                return None
            # Handle rate limiting
            elif (
                response.status_code == 403
                and "X-RateLimit-Remaining" in response.headers
            ):
                if int(response.headers["X-RateLimit-Remaining"]) == 0:
                    reset_time = int(response.headers["X-RateLimit-Reset"])
                    sleep_time = max(reset_time - time.time(), 0)
                    time.sleep(sleep_time)
                    continue
            # Altri status code indicano problemi con l'API, quindi continuiamo a riprovare

        except requests.ReadTimeout:
            continue
        except requests.ConnectionError:
            time.sleep(RETRY_DELAY)
            continue

    return None  # Tutti i tentativi falliti


def get_data_to_store(
    issue_title: str,
    issue_body: str,
    created_at: str,
    had_primary_source: str,
    user_id: int,
) -> dict:
    """Get structured data from issue content for storage.

    Args:
        issue_title: Title of the GitHub issue
        issue_body: Body content of the GitHub issue
        created_at: ISO timestamp when issue was created
        had_primary_source: URL of the original issue
        user_id: GitHub user ID of issue author

    Returns:
        Dictionary containing structured issue data and provenance information

    Raises:
        ValueError: If issue body cannot be split or CSV data is invalid
    """
    try:
        # Split and clean the data sections
        metadata_csv, citations_csv = [
            section.strip() for section in issue_body.split("===###===@@@===")
        ]

        metadata = list(csv.DictReader(io.StringIO(metadata_csv)))
        citations = list(csv.DictReader(io.StringIO(citations_csv)))

        # Validate required data
        if not metadata or not citations:
            raise ValueError("Empty metadata or citations section")

        return {
            "data": {
                "title": issue_title,
                "metadata": metadata,
                "citations": citations,
            },
            "provenance": {
                "generatedAtTime": created_at,
                "wasAttributedTo": user_id,
                "hadPrimarySource": had_primary_source,
            },
        }
    except Exception as e:
        raise ValueError(f"Failed to process issue data: {str(e)}")


def _get_zenodo_token() -> str:
    """Get the appropriate Zenodo token based on environment."""
    environment = os.environ.get("ENVIRONMENT", "development")
    if environment == "development":
        token = os.environ.get("ZENODO_SANDBOX")
        if not token:
            raise ValueError("ZENODO_SANDBOX token not found in environment")
        return token
    else:
        token = os.environ.get("ZENODO_PRODUCTION")
        if not token:
            raise ValueError("ZENODO_PRODUCTION token not found in environment")
        return token


def _create_deposition_resource(
    date: str, base_url: str = "https://zenodo.org/api"
) -> Tuple[str, str]:
    """Create a new deposition resource on Zenodo."""
    headers = {"Content-Type": "application/json"}

    metadata = {
        "metadata": {
            "upload_type": "dataset",
            "publication_date": date,
            "title": f"OpenCitations crowdsourcing: deposits of the week before {date}",
            "creators": [
                {
                    "name": "crocibot",
                    "affiliation": "Research Centre for Open Scholarly Metadata, Department of Classical Philology and Italian Studies, University of Bologna, Bologna, Italy",
                }
            ],
            "description": f"OpenCitations collects citation data and related metadata from the community through issues on the GitHub repository <a href='https://github.com/opencitations/crowdsourcing'>https://github.com/opencitations/crowdsourcing</a>. In order to preserve long-term provenance information, such data is uploaded to Zenodo every week. This upload contains the data of deposit issues published in the week before {date}.",
            "access_right": "open",
            "license": "CC0-1.0",
            "prereserve_doi": True,
            "keywords": [
                "OpenCitations",
                "crowdsourcing",
                "provenance",
                "GitHub issues",
            ],
            "related_identifiers": [
                {
                    "identifier": "https://github.com/opencitations/crowdsourcing",
                    "relation": "isDerivedFrom",
                    "resource_type": "dataset",
                }
            ],
            "version": "1.0.0",
        }
    }

    response = requests.post(
        f"{base_url}/deposit/depositions",
        params={"access_token": _get_zenodo_token()},
        json=metadata,
        headers=headers,
        timeout=30,
    )

    response.raise_for_status()
    data = response.json()

    return data["id"], data["links"]["bucket"]


def _upload_data(
    date: str, bucket: str, base_url: str = "https://zenodo.org/api"
) -> None:
    """Upload data file to Zenodo bucket."""
    filename = f"{date}_weekly_deposit.json"

    with open("data_to_store.json", "rb") as fp:
        response = requests.put(
            f"{bucket}/{filename}",
            data=fp,
            params={"access_token": _get_zenodo_token()},
            timeout=30,
        )
        response.raise_for_status()


def deposit_on_zenodo(data_to_store: List[dict]) -> None:
    """Deposit data on Zenodo based on environment."""
    environment = os.environ.get("ENVIRONMENT", "development")

    # In development, usa la Zenodo Sandbox
    if environment == "development":
        base_url = "https://sandbox.zenodo.org/api"
    else:
        base_url = "https://zenodo.org/api"

    try:
        # Salva i dati in un file temporaneo
        with open("data_to_store.json", "w") as f:
            json.dump(data_to_store, f)

        # Crea una nuova deposizione
        deposition_id, bucket = _create_deposition_resource(
            datetime.now().strftime("%Y-%m-%d"), base_url=base_url
        )

        # Carica i dati
        _upload_data(datetime.now().strftime("%Y-%m-%d"), bucket, base_url=base_url)

        # Pubblica la deposizione
        response = requests.post(
            f"{base_url}/deposit/depositions/{deposition_id}/actions/publish",
            params={"access_token": _get_zenodo_token()},
            timeout=30,
        )

        if response.status_code != 202:
            raise Exception(f"Failed to publish deposition: {response.text}")

    finally:
        # Pulisci i file temporanei
        if os.path.exists("data_to_store.json"):
            os.remove("data_to_store.json")


def is_in_safe_list(user_id: int) -> bool:
    """Check if a user ID is in the safe list.

    Args:
        user_id: GitHub user ID to check

    Returns:
        True if user is in safe list, False otherwise
    """
    try:
        with open("safe_list.txt", "r") as f:
            return str(user_id) in {line.strip() for line in f}
    except FileNotFoundError:
        return False


def get_open_issues() -> List[dict]:
    """Fetch open issues with 'deposit' label using GitHub REST API."""
    MAX_RETRIES = 3
    RETRY_DELAY = 5

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(
                "https://api.github.com/repos/opencitations/crowdsourcing/issues",
                params={
                    "state": "open",
                    "labels": "deposit",
                },
                headers=headers,
                timeout=30,
            )

            if response.status_code == 200:
                issues = response.json()
                return [
                    {
                        "title": issue["title"],
                        "body": issue["body"],
                        "number": str(issue["number"]),
                        "author": {"login": issue["user"]["login"]},
                        "createdAt": issue["created_at"],
                        "url": issue["html_url"],
                    }
                    for issue in issues
                ]

            elif response.status_code == 404:
                return []

            elif (
                response.status_code == 403
                and "X-RateLimit-Remaining" in response.headers
            ):
                if int(response.headers["X-RateLimit-Remaining"]) == 0:
                    reset_time = int(response.headers["X-RateLimit-Reset"])
                    current_time = time.time()
                    if (
                        reset_time > current_time
                    ):  # Verifica se il rate limit non è ancora scaduto
                        sleep_time = reset_time - current_time
                        time.sleep(sleep_time)
                        continue
                    # Se il rate limit è già scaduto, prova subito la prossima richiesta
                    continue

        except (requests.RequestException, KeyError) as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
                continue
            raise RuntimeError(
                f"Failed to fetch issues after {MAX_RETRIES} attempts"
            ) from e

    return []


def process_open_issues() -> None:
    """Process all open issues with detailed logging."""
    setup_logging()
    logger = logging.getLogger(__name__)

    try:
        logger.info("Starting to process open issues")
        issues = get_open_issues()
        logger.info(f"Found {len(issues)} open issues to process")

        data_to_store = list()

        for issue in issues:
            issue_number = issue["number"]
            logger.info(f"Processing issue #{issue_number}")

            username = issue["author"]["login"]
            logger.info(f"Getting user ID for {username}")
            user_id = get_user_id(username)
            logger.info(f"User ID for {username}: {user_id}")

            if not is_in_safe_list(user_id):
                logger.warning(f"User {username} (ID: {user_id}) not in safe list")
                answer(
                    False,
                    "To make a deposit, please contact OpenCitations at <contact@opencitations.net> to register as a trusted user",
                    issue_number,
                    is_authorized=False,
                )
                continue

            logger.info(f"User {username} is authorized")
            issue_title = issue["title"]
            issue_body = issue["body"]
            created_at = issue["createdAt"]
            had_primary_source = issue["url"]

            logger.info(f"Validating issue #{issue_number}")
            is_valid, message = validate(issue_title, issue_body)
            logger.info(
                f"Validation result for #{issue_number}: valid={is_valid}, message={message}"
            )

            answer(is_valid, message, issue_number, is_authorized=True)
            logger.info(f"Posted answer to issue #{issue_number}")

            if is_valid:
                logger.info(f"Getting data to store for issue #{issue_number}")
                try:
                    issue_data = get_data_to_store(
                        issue_title, issue_body, created_at, had_primary_source, user_id
                    )
                    data_to_store.append(issue_data)
                    logger.info(
                        f"Successfully processed data for issue #{issue_number}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error processing data for issue #{issue_number}: {e}"
                    )
                    continue

        if data_to_store:
            logger.info(f"Attempting to deposit {len(data_to_store)} items to Zenodo")
            try:
                deposit_on_zenodo(data_to_store)
                logger.info("Successfully deposited data to Zenodo")
            except Exception as e:
                logger.error(f"Failed to deposit data to Zenodo: {e}")
                raise
        else:
            logger.info("No valid data to deposit to Zenodo")

    except Exception as e:
        logger.error(f"Error processing issues: {e}", exc_info=True)
        raise
    finally:
        logger.info("Completed processing open issues")


if __name__ == "__main__":  # pragma: no cover
    process_open_issues()
