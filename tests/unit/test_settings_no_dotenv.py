from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_isolated_process_cannot_rehydrate_hostile_dotenv_secrets(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text(
        "SALESFORCE_CLIENT_SECRET=hostile-salesforce-secret\n"
        "MIP_GENIE_ACTION_SECRET_CURRENT=hostile-genie-secret\n"
        "MIP_LENDER_NAME=Hostile Dotenv Lender\n",
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "SALESFORCE_CLIENT_SECRET",
            "MIP_GENIE_ACTION_SECRET_CURRENT",
            "MIP_LENDER_NAME",
        }
    }
    env.update(
        {
            "MIP_DISABLE_DOTENV": "1",
            "PYTHONPATH": str(REPO),
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from backend.config.settings import Settings, settings; "
                "assert settings.salesforce_client_secret is None; "
                "assert settings.mip_genie_action_secret_current is None; "
                "assert settings.mip_lender_name == 'Summit Mortgage'; "
                "direct = Settings(); "
                "assert direct.salesforce_client_secret is None; "
                "assert direct.mip_genie_action_secret_current is None; "
                "assert direct.mip_lender_name == 'Summit Mortgage'"
            ),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
