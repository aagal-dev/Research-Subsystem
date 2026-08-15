def clean_connector_results(raw_results):
    """
    Generic post-parallel connector-result cleaner.

    Accepts:
        dict / list / tuple / str / None

    Returns:
        A normalized list containing only usable evidence.
    """

    cleaned = []

    CONTAINER_KEYS = {
        "results",
        "pages",
        "items",
        "documents",
        "sources",
        "evidence",
        "data",
    }

    TEXT_KEYS = (
        "text",
        "content",
        "transcript",
        "summary",
        "abstract",
        "description",
    )

    def looks_like_error(text):
        if not isinstance(text, str):
            return False

        text = text.strip().lower()

        return (
            text.startswith("error:")
            or text.startswith("request error")
            or text.startswith("http error")
            or text.startswith("failed:")
            or "exception:" in text
        )

    def add_evidence(value, connector=None, query=None):
        text = None

        for key in TEXT_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                text = candidate.strip()
                break

        if not text or looks_like_error(text):
            return False

        quality = value.get("quality")

        if isinstance(quality, dict):
            if quality.get("usable") is False:
                return False

        cleaned.append({
            "connector": connector,
            "query": query,

            "title": (
                value.get("title")
                or value.get("name")
            ),

            "url": (
                value.get("final_url")
                or value.get("url")
                or value.get("link")
            ),

            "text": text,

            "source": value.get("source") or {},
            "quality": quality or {},
            "evidence": value.get("evidence") or {},

            "metadata": {
                k: v
                for k, v in value.items()
                if k not in {
                    "ok",
                    "status",
                    "connector",
                    "query",
                    "title",
                    "name",
                    "url",
                    "final_url",
                    "link",
                    *TEXT_KEYS,
                    "source",
                    "quality",
                    "evidence",
                    "error",
                }
                and v is not None
            },
        })

        return True

    def walk(value, connector=None, query=None):
        if value is None:
            return

        # ---------------------------------------------------------
        # STR
        # ---------------------------------------------------------
        if isinstance(value, str):
            text = value.strip()

            if text and not looks_like_error(text):
                cleaned.append({
                    "connector": connector,
                    "query": query,
                    "text": text,
                    "source": {},
                    "quality": {},
                    "evidence": {},
                    "metadata": {},
                })

            return

        # ---------------------------------------------------------
        # LIST / TUPLE
        # ---------------------------------------------------------
        if isinstance(value, (list, tuple)):
            for item in value:
                walk(item, connector, query)

            return

        # ---------------------------------------------------------
        # DICT
        # ---------------------------------------------------------
        if not isinstance(value, dict):
            return

        connector = value.get("connector") or connector
        query = value.get("query") or query

        # Explicit failure
        if value.get("ok") is False:
            return

        if value.get("status") in {
            "failed",
            "error",
        }:
            return

        if value.get("error"):
            return

        # First try to interpret this dict as
        # an actual evidence object.
        if add_evidence(value, connector, query):
            return

        # Then inspect known containers.
        for key in CONTAINER_KEYS:
            nested = value.get(key)

            if nested is not None:
                walk(nested, connector, query)

        # CRITICAL:
        # If the structure is an arbitrary dictionary
        # like {"r1": "paper_1", "r2": "paper_2"},
        # recursively inspect its values too.
        for key, nested in value.items():

            if key in CONTAINER_KEYS:
                continue

            if key in {
                "ok",
                "status",
                "connector",
                "query",
                "error",
                "quality",
            }:
                continue

            if isinstance(nested, (dict, list, tuple, str)):
                walk(nested, connector, query)

    for raw in raw_results or []:
        walk(raw)

    return cleaned