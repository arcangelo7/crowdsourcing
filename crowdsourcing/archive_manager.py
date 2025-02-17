#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright 2025 Arcangelo Massari <arcangelo.massari@unibo.it>
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

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
import yaml
from crowdsourcing.zenodo_utils import (
    create_deposition_resource,
    get_zenodo_base_url,
    get_zenodo_token,
)

# Configure logging
logger = logging.getLogger(__name__)


class ArchiveManager:
    """Manages the archival of validation reports to Zenodo."""

    def __init__(self, config_path: str = "archive_config.yaml"):
        """Initialize the archive manager.

        Args:
            config_path: Path to the archive configuration file
        """
        self.config_path = config_path
        self.config = self._load_config()
        self.index_path = Path(self.config["validation_reports"]["index_file"])
        self.reports_dir = Path(self.config["validation_reports"]["reports_dir"])

        # Create reports directory if it doesn't exist
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        # Create index file if it doesn't exist
        if not self.index_path.exists():
            self._init_index()

    def _load_config(self) -> dict:
        """Load the archive configuration file.

        Raises:
            FileNotFoundError: If the configuration file does not exist
        """
        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def _init_index(self) -> None:
        """Initialize the index file if it doesn't exist."""
        index_data = {
            "github_reports": {},  # filename -> github_url
            "zenodo_reports": {},  # filename -> {"url": direct_file_url, "doi": doi_url}
            "last_archive": None,  # timestamp of last archive
        }

        with open(self.index_path, "w") as f:
            json.dump(index_data, f, indent=2)

    def _load_index(self) -> dict:
        """Load the current index file."""
        with open(self.index_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_index(self, index_data: dict) -> None:
        """Save the index file."""
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(index_data, f, indent=2)

    def add_report(self, report_filename: str, github_url: str) -> None:
        """Add a new report to the index.

        Args:
            report_filename: Name of the report file
            github_url: GitHub Pages URL where the report is hosted
        """
        # Ensure reports directory exists
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        # Ensure index exists (needed when directory is recreated)
        if not self.index_path.exists():
            self._init_index()

        index_data = self._load_index()
        index_data["github_reports"][report_filename] = github_url
        self._save_index(index_data)

    def archive_reports(self) -> Optional[str]:
        """Archive reports to Zenodo when threshold is reached.

        Returns:
            The DOI of the created Zenodo deposit, or None if no archival was needed
        """
        index_data = self._load_index()
        github_reports = index_data["github_reports"]

        # Get all reports to archive
        reports_to_archive = sorted(
            github_reports.keys(),
            key=lambda x: (
                os.path.getctime(os.path.join(self.reports_dir, x))
                if os.path.exists(os.path.join(self.reports_dir, x))
                else 0
            ),
        )

        if not reports_to_archive:
            return None

        try:
            # Create Zenodo deposition
            base_url = get_zenodo_base_url()
            date = datetime.now().strftime("%Y-%m-%d")
            metadata = self.config["zenodo"]["metadata_template"].copy()

            # Create a meaningful title with number of reports and date range
            first_report_date = min(
                datetime.fromtimestamp(
                    os.path.getctime(os.path.join(self.reports_dir, x))
                ).strftime("%Y-%m-%d")
                for x in reports_to_archive
                if os.path.exists(os.path.join(self.reports_dir, x))
            )
            last_report_date = max(
                datetime.fromtimestamp(
                    os.path.getctime(os.path.join(self.reports_dir, x))
                ).strftime("%Y-%m-%d")
                for x in reports_to_archive
                if os.path.exists(os.path.join(self.reports_dir, x))
            )
            date_range = (
                f"from {first_report_date} to {last_report_date}"
                if first_report_date != last_report_date
                else f"on {first_report_date}"
            )

            # Extract issue numbers from filenames
            issue_numbers = []
            for report in reports_to_archive:
                match = re.search(r"validation_issue_(\d+)\.html", report)
                if match:
                    issue_numbers.append(int(match.group(1)))

            min_issue = min(issue_numbers) if issue_numbers else 0
            max_issue = max(issue_numbers) if issue_numbers else 0

            metadata["title"] = (
                f"OpenCitations validation reports: issues #{min_issue} to #{max_issue} ({date_range})"
            )
            metadata["description"] = (
                f"This deposit contains {len(reports_to_archive)} validation reports generated {date_range} to validate citation data and metadata submitted through GitHub issues in the OpenCitations crowdsourcing repository."
            )
            metadata["publication_date"] = date

            deposition_id, bucket_url = create_deposition_resource(
                date=date,
                metadata=metadata,
                base_url=base_url,
            )

            # Upload reports to Zenodo
            for report in reports_to_archive:
                report_path = os.path.join(self.reports_dir, report)

                with open(report_path, "r", encoding="utf-8") as f:
                    r = requests.put(
                        f"{bucket_url}/{report}",
                        data=f,
                        params={"access_token": get_zenodo_token()},
                    )
                    r.raise_for_status()

            # Publish the deposition
            r = requests.post(
                f"{base_url}/deposit/depositions/{deposition_id}/actions/publish",
                params={"access_token": get_zenodo_token()},
            )
            r.raise_for_status()

            response_data = r.json()
            doi = response_data["doi"]
            # Get the base URL for files (remove /api from base_url)
            files_base_url = base_url

            # Update index
            for report in reports_to_archive:
                report_path = os.path.join(self.reports_dir, report)
                # Move from github_reports to zenodo_reports
                github_url = github_reports.pop(report)
                index_data["zenodo_reports"][report] = {
                    "url": f"{files_base_url}/records/{deposition_id}/files/{report}/content",
                    "doi": f"https://doi.org/{doi}",
                }
                # Delete the report file
                os.remove(report_path)

            index_data["last_archive"] = datetime.now().isoformat()
            self._save_index(index_data)

            return doi

        except Exception as e:
            logger.error(f"Failed to archive reports: {e}")
            raise

    def get_report_url(self, report_filename: str) -> Optional[str]:
        """Get the current URL for a report.

        Args:
            report_filename: Name of the report file

        Returns:
            URL where the report can be found (either on GitHub or Zenodo)
        """
        index_data = self._load_index()

        # Check GitHub reports first
        if report_filename in index_data["github_reports"]:
            return index_data["github_reports"][report_filename]

        # Then check Zenodo - return direct URL if available
        if report_filename in index_data["zenodo_reports"]:
            zenodo_data = index_data["zenodo_reports"][report_filename]
            return zenodo_data["url"]  # Return direct URL as primary choice

        return None

    def needs_archival(self) -> bool:
        """Check if reports need to be archived based on configuration threshold.

        Returns:
            bool: True if number of reports exceeds threshold, False otherwise
        """
        index_data = self._load_index()
        return (
            len(index_data["github_reports"])
            >= self.config["validation_reports"]["max_reports_before_archive"]
        )
