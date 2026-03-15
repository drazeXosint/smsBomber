from __future__ import annotations

import json
from typing import List, Dict, Any, Tuple, Optional

from apis import API_CONFIGS as _BASE_CONFIGS
from bot.services.database import db


class ApiManager:
    """
    Single source of truth for all API configs.
    Merges the static apis.py list with admin-added APIs from the database.
    No restart needed when APIs are added or deleted via the admin panel.
    """

    def getMergedConfigs(self) -> List[Dict[str, Any]]:
        """
        Returns merged API list. Custom APIs added via bot override base APIs
        with the same name. Purely custom APIs are appended at the end.
        """
        customApis = db.getAllCustomApis()

        # Build lookup by name (lowercased) and url for override detection
        customByName: Dict[str, Dict] = {}
        customByUrl:  Dict[str, Dict] = {}
        for row in customApis:
            try:
                cfg = json.loads(row["configJson"])
                customByName[cfg.get("name", "").lower()] = cfg
                customByUrl[cfg.get("url", "")]           = cfg
            except Exception:
                pass

        result    = []
        seenNames = set()

        # Go through base configs — replace with custom version if override exists
        for base in _BASE_CONFIGS:
            key = base["name"].lower()
            override = customByName.get(key) or customByUrl.get(base["url"])
            if override:
                result.append(override)
                seenNames.add(override.get("name", "").lower())
            else:
                result.append(base)
                seenNames.add(key)

        # Add purely custom APIs (not overrides of base)
        for row in customApis:
            try:
                cfg = json.loads(row["configJson"])
                if cfg.get("name", "").lower() not in seenNames:
                    result.append(cfg)
            except Exception:
                pass

        # Filter out skipped APIs
        skipped = db.getSkippedApiNames()
        result  = [cfg for cfg in result if cfg.get("name", "") not in skipped]

        return result

    def validateApiJson(self, raw: str) -> Tuple[bool, Optional[Dict], str]:
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            lines = [l for l in lines if not l.startswith("```")]
            raw = "\n".join(lines).strip()

        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError as e:
            return False, None, f"Invalid JSON: {e}"

        if not isinstance(cfg, dict):
            return False, None, "JSON must be an object, not a list or value."

        for field in ["name", "method", "url"]:
            if field not in cfg:
                return False, None, f"Missing required field: \"{field}\""

        if cfg["method"].upper() not in ["GET", "POST", "PUT", "PATCH", "DELETE"]:
            return False, None, f"Invalid method: {cfg['method']}"

        if not cfg["url"].startswith("http"):
            return False, None, "URL must start with http:// or https://"

        cfg["method"] = cfg["method"].upper()
        return True, cfg, ""

    def formatApiPreview(self, cfg: dict) -> str:
        lines = [
            "API Preview\n",
            f"Name   : {cfg['name']}",
            f"Method : {cfg['method']}",
            f"URL    : {cfg['url']}",
        ]
        if cfg.get("headers"):
            lines.append(f"Headers: {len(cfg['headers'])} defined")
        if cfg.get("json"):
            lines.append(f"Body   : JSON ({len(cfg['json'])} fields)")
        elif cfg.get("data"):
            lines.append(f"Body   : Form data ({len(cfg['data'])} fields)")
        if cfg.get("params"):
            lines.append(f"Params : {len(cfg['params'])} defined")
        return "\n".join(lines)


apiManager = ApiManager()