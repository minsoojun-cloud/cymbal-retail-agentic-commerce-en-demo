"""AI Commerce Search (Retail API v2alpha) + local fallback engine.

Both engines return the same response shape (CONTRACT.md):
  {results:[Product], total, facets:[{key,label,values:[{value,label,count,selected}],dynamic}],
   tiles:[{label,filter,key,value,image}],
   conversation:{id, refined_query, followup_question, suggested_answers:[str], answer_actions:[...], source},
   corrected_query, applied_filter, engine, features:{name: live|fallback|off}}

Every advanced Retail feature (query expansion, spell correction, dynamic facets, explicit facet specs,
tile navigation, conversational search) is optional: if the API rejects a request we probe the features
individually, disable the failing ones (for FEATURE_RETRY_SEC) and retry without them. Missing UI data is
then synthesized (tiles from top facet values, follow-up question/answers from facets — upgraded by Gemini
in /api/ai-summary).
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Any

from .catalog import (FACET_LABELS, PRICE_INTERVALS, Catalog, parse_price_value, price_interval_label,
                      price_interval_value, public)

log = logging.getLogger("cymbal-site.search")

PROJECT_ID = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
PROJECT_NUMBER = os.environ.get("PROJECT_NUMBER", "")
LOCATION = os.environ.get("RETAIL_LOCATION", "global")
CATALOG_ID = os.environ.get("CATALOG", "default_catalog")
BRANCH = os.environ.get("BRANCH", "0")
SERVING_CONFIG = os.environ.get("SERVING_CONFIG", "default_search")
FEATURE_RETRY_SEC = int(os.environ.get("FEATURE_RETRY_SEC", "600"))
CONV_TIMEOUT = float(os.environ.get("CONV_TIMEOUT_SEC", "12"))

ADVANCED = ["query_expansion", "spell_correction", "dynamic_facets", "facet_specs", "tile_navigation",
            "conversational"]
TEXT_FACETS = ["attributes.top_category", "brands", "attributes.on_sale", "attributes.labels"]
PICKUP_FACET = "pickupInStore"  # Retail fulfillment facet: values = store ids (placeId) with pickup-in-store
TILE_KEYS = ["categories", "attributes.labels", "brands"]


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------
def value_label(key: str, value: str) -> str:
    if key == "attributes.on_sale":
        return {"true": "On sale", "false": "Regular price"}.get(value, value)
    if key == "attributes.is_seasonal":
        return {"true": "In season", "false": "Year-round"}.get(value, value)
    if key == "attributes.channel":
        return {"omni": "Online & in store", "store_only": "In-store only", "online_only": "Online only"}.get(value, value)
    if key == PICKUP_FACET:
        from .stores import directory
        return directory().short_name(value)
    if key == "categories":
        return value.split(">")[-1].strip()
    if key == "price":
        lo, hi = parse_price_value(value)
        return price_interval_label(lo, hi)
    return value


def _q(v: str) -> str:
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def facets_to_filter(facets: dict[str, list[str]] | None) -> str:
    parts = []
    for key, vals in (facets or {}).items():
        vals = [v for v in (vals or []) if v not in (None, "")]
        if not vals:
            continue
        if key == "price":
            ivs = []
            for v in vals:
                lo, hi = parse_price_value(v)
                ivs.append(f"price: IN({int(lo)}i, {'*' if hi is None else str(int(hi)) + 'e'})")
            parts.append("(" + " OR ".join(ivs) + ")" if len(ivs) > 1 else ivs[0])
        else:
            parts.append(f"{key}: ANY({', '.join(_q(v) for v in vals)})")
    return " AND ".join(parts)


def merge_filters(*filters: str) -> str:
    fs = [f.strip() for f in filters if f and f.strip()]
    if not fs:
        return ""
    if len(fs) == 1:
        return fs[0]
    return " AND ".join(f"({f})" for f in fs)


def merge_tiles_into_facets(facets: dict[str, list[str]], tiles: list[dict]) -> dict[str, list[str]]:
    out = {k: list(v) for k, v in (facets or {}).items()}
    for t in tiles or []:
        k, v = t.get("key"), t.get("value")
        if k and v and v not in out.setdefault(k, []):
            out[k].append(v)
    return out


def heuristic_conversation(q: str, facets: list[dict], selected: dict[str, list[str]], conv_id: str = "",
                           source: str = "heuristic") -> dict:
    """Follow-up question + answer chips derived from facet distribution (fallback for conversational search)."""
    by_key = {f["key"]: f for f in facets}
    plans = [
        ("attributes.top_category", "Which category are you shopping for?"),
        ("attributes.labels", "Anything specific you're looking for?"),
        ("brands", "Do you have a preferred brand?"),
        ("price", "What's your budget?"),
    ]
    for key, question in plans:
        if selected.get(key):
            continue
        f = by_key.get(key)
        if not f:
            continue
        vals = [v for v in f["values"] if v.get("count", 0) > 0 and not v.get("selected")]
        if key == "attributes.labels":
            vals = sorted(vals, key=lambda v: -v.get("count", 0))
        if len(vals) < 2:
            continue
        acts = [{"label": v.get("label") or v["value"], "kind": "facet", "key": key, "value": v["value"]}
                for v in vals[:6]]
        return {"id": conv_id or ("local-" + uuid.uuid4().hex[:12]), "refined_query": q, "followup_question": question,
                "suggested_answers": [a["label"] for a in acts], "answer_actions": acts, "source": source}
    return {"id": conv_id or "", "refined_query": q, "followup_question": "", "suggested_answers": [],
            "answer_actions": [], "source": source}


def tiles_from_facets(facets: list[dict], selected: dict[str, list[str]], catalog: Catalog | None = None,
                      results: list[dict] | None = None) -> list[dict]:
    """Fallback tile navigation: the most frequent unselected facet values (sub-category first)."""
    tiles: list[dict] = []
    by_key = {f["key"]: f for f in facets}
    for key in TILE_KEYS + ["attributes.top_category"]:
        f = by_key.get(key)
        if not f:
            continue
        for v in sorted(f["values"], key=lambda v: -v.get("count", 0)):
            if v.get("selected") or v["value"] in (selected.get(key) or []):
                continue
            tiles.append({"label": v.get("label") or value_label(key, v["value"]), "key": key, "value": v["value"],
                          "filter": f"{key}: ANY({_q(v['value'])})", "count": v.get("count", 0), "image": ""})
            if len([t for t in tiles if t["key"] == key]) >= 6:
                break
        if len(tiles) >= 12:
            break
    # representative image per tile from current results
    for t in tiles:
        for p in results or []:
            if catalog is not None:
                n = catalog.get(p["id"])
                if n and t["value"] in catalog.facet_values(n, t["key"]) and p.get("image"):
                    t["image"] = p["image"]
                    break
    seen, out = set(), []
    for t in tiles:
        if t["label"] in seen:
            continue
        seen.add(t["label"])
        out.append(t)
    return out[:12]


def _sort_facet(key: str, values: list[dict]) -> list[dict]:
    if key == "price":
        return sorted(values, key=lambda v: parse_price_value(v["value"])[0])
    if key == "attributes.on_sale":
        return sorted(values, key=lambda v: v["value"] != "true")
    return sorted(values, key=lambda v: -v.get("count", 0))


# ---------------------------------------------------------------------------
# Local engine (DATA_FILE mode / offline)
# ---------------------------------------------------------------------------
class LocalSearch:
    mode = "local"

    def __init__(self, catalog: Catalog):
        from .catalog import Suggestions, default_suggestions_file

        self.catalog = catalog
        self.suggestions = Suggestions(default_suggestions_file())
        from .catalog import build_vocab
        self.vocab = build_vocab(catalog, self.suggestions)
        try:  # "Pick up in store" facet from the local store inventory (store_only / omni products)
            from .stores import directory
            d = directory()
            for n in catalog.items.values():
                n["_pickup"] = [] if n.get("channel") == "online_only" else d.pickup_store_ids(n["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("store inventory index unavailable: %s", e)

    def feature_status(self) -> dict:
        return {f: "mock" for f in ADVANCED} | {"autocomplete": "mock"}

    def hydrate(self, ids: list[str]) -> list[dict]:
        return self.catalog.get_many(ids)

    def search(self, q: str = "", filter_: str = "", facets: dict | None = None, tiles: list | None = None,
               conversation_id: str = "", answer: str = "", answer_key: str = "", answer_value: str = "",
               page: int = 1, page_size: int = 24, visitor_id: str = "", rq: str = "") -> dict:
        selected = merge_tiles_into_facets(facets or {}, tiles or [])
        if rq:
            q = rq
        if answer_key and answer_value and answer_value not in selected.setdefault(answer_key, []):
            selected[answer_key].append(answer_value)
        elif answer and not answer_key:
            q = (q + " " + answer).strip()
        want_sale = "on_sale" in (filter_ or "") and "true" in (filter_ or "")
        import re as _re
        m = _re.search(r'pickupInStore:\s*ANY\(([^)]*)\)', filter_ or "")
        if m:
            for sid in _re.findall(r'"([^"]+)"', m.group(1)):
                if sid not in selected.setdefault(PICKUP_FACET, []):
                    selected[PICKUP_FACET].append(sid)
        corrected = ""

        def run(query: str):
            base_ = [n for _, n in self.catalog.text_search(query) if not want_sale or n["on_sale"]]
            return base_, [n for n in base_ if self.catalog.matches_facets(n, selected)]

        base, hits = run(q)
        if not base:
            from .catalog import keyword_terms
            for term in keyword_terms(q, self.vocab, limit=5):
                kb, kh = run(term)
                if kb:
                    base, hits, q = kb, kh, term
                    break
        start = max(0, (page - 1) * page_size)
        results = [public(n) for n in hits[start:start + page_size]]
        facets_out = self._facets(base, selected)
        return {
            "results": results,
            "total": len(hits),
            "facets": facets_out,
            "tiles": tiles_from_facets(facets_out, selected, self.catalog, results),
            "conversation": heuristic_conversation(q, facets_out, selected, conversation_id, source="mock"),
            "conversation_turn": not rq and page == 1,
            "results_query": q,
            "corrected_query": corrected,
            "applied_filter": merge_filters(filter_, facets_to_filter(selected)),
            "engine": "local",
            "features": self.feature_status(),
        }

    def _facets(self, base: list[dict], selected: dict[str, list[str]]) -> list[dict]:
        out = []
        for key in TEXT_FACETS + [PICKUP_FACET, "categories", "price"]:
            # disjunctive counts: apply all other selected facets except this one
            others = {k: v for k, v in selected.items() if k != key}
            pool = [n for n in base if self.catalog.matches_facets(n, others)]
            counts: dict[str, int] = {}
            if key == "price":
                for lo, hi in PRICE_INTERVALS:
                    c = sum(1 for n in pool if n["price"] >= lo and (hi is None or n["price"] < hi))
                    if c:
                        counts[price_interval_value(lo, hi)] = c
            else:
                for n in pool:
                    for v in self.catalog.facet_values(n, key):
                        counts[v] = counts.get(v, 0) + 1
            if not counts:
                continue
            vals = [{"value": v, "label": value_label(key, v), "count": c, "selected": v in (selected.get(key) or [])}
                    for v, c in counts.items()]
            out.append({"key": key, "label": FACET_LABELS.get(key, key), "values": _sort_facet(key, vals)[:12],
                        "dynamic": key not in ("price", "brands")})
        return [f for f in out if f["key"] != "categories"] + [f for f in out if f["key"] == "categories"]

    def autocomplete(self, q: str, visitor_id: str = "") -> dict:
        return {"suggestions": self.suggestions.match(q) or self.catalog.suggest(q), "source": "local"}


# ---------------------------------------------------------------------------
# Retail engine (live)
# ---------------------------------------------------------------------------
class RetailSearch:
    mode = "retail"

    def __init__(self, catalog: Catalog):
        from google.cloud import retail_v2alpha as retail  # lazy

        self.retail = retail
        self.catalog = catalog
        self.local = LocalSearch(catalog)
        self.client = retail.SearchServiceClient()
        self.completion = retail.CompletionServiceClient()
        self.products = retail.ProductServiceClient()
        self.conv_client = retail.ConversationalSearchServiceClient()
        proj = PROJECT_NUMBER or PROJECT_ID
        self.catalog_name = f"projects/{proj}/locations/{LOCATION}/catalogs/{CATALOG_ID}"
        self.placement = f"{self.catalog_name}/servingConfigs/{SERVING_CONFIG}"
        self.branch = f"{self.catalog_name}/branches/{BRANCH}"
        self.disabled: dict[str, float] = {}
        self.status: dict[str, str] = {f: "untested" for f in ADVANCED} | {"autocomplete": "untested"}
        self.conv_answers: dict[str, dict[str, tuple[str, str]]] = {}
        self.pcache: dict[str, tuple[float, dict | None]] = {}
        self.lock = threading.Lock()

    # ---- feature management ----
    def enabled(self) -> list[str]:
        now = time.time()
        return [f for f in ADVANCED if now - self.disabled.get(f, 0) > FEATURE_RETRY_SEC]

    def feature_status(self) -> dict:
        return dict(self.status)

    def _build(self, feats: list[str], q: str, filter_: str, applied_tiles: list[dict], conversation_id: str,
               user_answer: dict | None, page_size: int, offset: int, visitor_id: str):
        r = self.retail
        kw: dict[str, Any] = dict(placement=self.placement, branch=self.branch, query=q,
                                  visitor_id=visitor_id or "cymbal-site", filter=filter_,
                                  page_size=page_size, offset=offset)
        if "query_expansion" in feats:
            kw["query_expansion_spec"] = {"condition": "AUTO", "pin_unexpanded_results": True}
        if "spell_correction" in feats:
            kw["spell_correction_spec"] = {"mode": "AUTO"}
        if "dynamic_facets" in feats:
            kw["dynamic_facet_spec"] = {"mode": "ENABLED"}
        if "facet_specs" in feats:
            specs = []
            for key in TEXT_FACETS:
                specs.append({"facet_key": {"key": key}, "limit": 12, "enable_dynamic_position": False})
            try:  # fulfillment facets need restricted values (place ids)
                from .stores import directory
                place_ids = [st["store_id"] for st in directory().stores() if st.get("store_id")]
            except Exception:  # noqa: BLE001
                place_ids = []
            if place_ids:
                specs.append({"facet_key": {"key": PICKUP_FACET, "restricted_values": place_ids}, "limit": 10,
                              "enable_dynamic_position": False})
            specs.append({"facet_key": {"key": "categories"}, "limit": 20, "enable_dynamic_position": False})
            specs.append({"facet_key": {"key": "price", "intervals": [
                {"minimum": lo, **({"exclusive_maximum": hi} if hi is not None else {})} for lo, hi in PRICE_INTERVALS]},
                "limit": 10})
            kw["facet_specs"] = specs
        if "tile_navigation" in feats:
            tiles = []
            for t in applied_tiles or []:
                if t.get("key") and t.get("value") and t["key"] != "price":
                    tiles.append(r.Tile(product_attribute_value=r.ProductAttributeValue(name=t["key"].replace("attributes.", ""), value=t["value"])))
            kw["tile_navigation_spec"] = {"tile_navigation_requested": True, "applied_tiles": tiles}
        if "conversational" in feats:
            spec: dict[str, Any] = {"followup_conversation_requested": True}
            if conversation_id and not conversation_id.startswith("local-"):
                spec["conversation_id"] = conversation_id
            if user_answer:
                spec["user_answer"] = user_answer
            kw["conversational_search_spec"] = spec
        return r.SearchRequest(**kw)

    def _call(self, feats: list[str], *a) -> Any:
        req = self._build(feats, *a)
        return self.client.search(request=req, timeout=20)

    def _search_with_fallback(self, *a) -> tuple[Any, list[str]]:
        feats = self.enabled()
        try:
            resp = self._call(feats, *a)
            for f in feats:
                if self.status.get(f) != "live":
                    self.status[f] = "live"
            return resp, feats
        except Exception as e:  # noqa: BLE001
            log.warning("search with %s failed: %s", feats, e)
            if not feats:
                raise
        # base request (no advanced features) must work, otherwise the index is not ready
        base_resp = self._call([], *a)
        ok = []
        for f in feats:
            try:
                self._call([f], *a)
                ok.append(f)
                self.status[f] = "live"
            except Exception as e:  # noqa: BLE001
                log.warning("feature %s rejected: %s", f, str(e)[:300])
                self.disabled[f] = time.time()
                self.status[f] = "fallback"
        if not ok:
            return base_resp, []
        try:
            return self._call(ok, *a), ok
        except Exception as e:  # noqa: BLE001  (combination failure – keep base)
            log.warning("combined features %s failed: %s", ok, e)
            return base_resp, []

    # ---- hydration ----
    def _get_product(self, pid: str) -> dict | None:
        hit = self.pcache.get(pid)
        if hit and time.time() - hit[0] < 1800:
            return hit[1]
        from google.protobuf.json_format import MessageToDict
        from .catalog import normalize
        try:
            p = self.products.get_product(name=f"{self.branch}/products/{pid}", timeout=10)
            n = public(normalize(MessageToDict(self.retail.Product.pb(p))))
        except Exception as e:  # noqa: BLE001
            log.warning("get_product %s failed: %s", pid, e)
            n = None
        self.pcache[pid] = (time.time(), n)
        return n

    def hydrate(self, ids: list[str]) -> list[dict]:
        out = []
        for i in ids:
            n = self.catalog.get(i)
            out.append(public(n) if n else self._get_product(i))
        return [x for x in out if x]

    # ---- live conversational search RPC (servingConfigs/*:conversationalSearch) ----
    def _conv_rpc(self, query: str, conversation_id: str, visitor_id: str) -> dict | None:
        if not getattr(self, "conv_client", None) or not query:
            return None
        req = self.retail.ConversationalSearchRequest(
            placement=self.placement, branch=self.branch, query=query, visitor_id=visitor_id or "cymbal-site",
            conversation_id=conversation_id or "",
            conversational_filtering_spec={"enable_conversational_filtering": True})
        out = {"conversation_id": "", "refined": [], "followup_en": "", "text_en": "", "suggested": [],
               "query_types": [], "state": ""}
        for ch in self.conv_client.conversational_search(request=req, timeout=CONV_TIMEOUT):
            if ch.conversation_id:
                out["conversation_id"] = ch.conversation_id
            if ch.refined_search:
                out["refined"] = [r.query for r in ch.refined_search if r.query]
            if ch.conversational_text_response:
                out["text_en"] = ch.conversational_text_response
            fq = ch.followup_question
            if fq and fq.followup_question:
                out["followup_en"] = fq.followup_question
            if fq and fq.suggested_answers:
                out["suggested"] = [a.product_attribute_value.value for a in fq.suggested_answers
                                    if a.product_attribute_value.value]
            if ch.user_query_types:
                out["query_types"] = list(ch.user_query_types)
            if ch.state:
                out["state"] = ch.state.name if hasattr(ch.state, "name") else str(ch.state)
        return out if (out["conversation_id"] or out["refined"]) else None

    # ---- search ----
    def search(self, q: str = "", filter_: str = "", facets: dict | None = None, tiles: list | None = None,
               conversation_id: str = "", answer: str = "", answer_key: str = "", answer_value: str = "",
               page: int = 1, page_size: int = 24, visitor_id: str = "", rq: str = "") -> dict:
        from concurrent.futures import ThreadPoolExecutor


        selected = {k: list(v) for k, v in (facets or {}).items() if v}
        tiles = tiles or []
        tile_facets = merge_tiles_into_facets({}, tiles)  # applied tiles are always enforced as filters
        if answer and answer_key and answer_value and answer_value not in selected.setdefault(answer_key, []):
            selected[answer_key].append(answer_value)
        filt = merge_filters(filter_, facets_to_filter(merge_tiles_into_facets(selected, tiles)))
        offset = max(0, (page - 1) * page_size)
        live_cid = conversation_id if conversation_id and not conversation_id.startswith("local-") else ""

        # which conversational turn (if any) this request represents
        turn, turn_cid = None, ""
        if rq:
            base_query = rq                       # follow-up request (facet / tile / page) on a refined query
        elif answer and not answer_key:
            turn, turn_cid = answer, live_cid     # user picked / typed an answer → next turn
            base_query = (q + " " + answer).strip()
        else:
            base_query = q
            if page == 1 and q and not answer:
                turn = q                          # new query → first turn
        args = (filt, tiles, live_cid, None, page_size, offset, visitor_id)

        conv_res = None
        try:
            with ThreadPoolExecutor(max_workers=3) as ex:
                fconv = ex.submit(self._conv_rpc, turn, turn_cid, visitor_id) if turn else None
                fbase = ex.submit(self._search_with_fallback, base_query, *args)
                if fconv is not None:
                    try:
                        conv_res = fconv.result(timeout=CONV_TIMEOUT + 2)
                        self.status["conversational"] = "live" if conv_res else "fallback"
                    except Exception as e:  # noqa: BLE001
                        log.warning("conversationalSearch failed: %s", str(e)[:300])
                        self.status["conversational"] = "fallback"
                resp, feats = fbase.result()
                query_for_api = base_query
                refined = (conv_res or {}).get("refined") or []
                cands = [x for x in refined[:4] if x and x != base_query]
                if cands:
                    futs = [ex.submit(self._search_with_fallback, x, *args) for x in cands]
                    for x, fu in zip(cands, futs):
                        try:
                            r2, f2 = fu.result(timeout=15)
                        except Exception as e:  # noqa: BLE001
                            log.warning("refined search %s failed: %s", x, e)
                            continue
                        if len(r2.results):
                            resp, feats, query_for_api = r2, f2, x
                            break
        except Exception as e:  # noqa: BLE001
            log.error("Retail search unavailable, using local engine: %s", e)
            out = self.local.search(q, filter_, facets, tiles, conversation_id, answer, answer_key, answer_value,
                                    page, page_size, visitor_id, rq)
            out["engine"] = "local-fallback"
            out["engine_error"] = str(e)[:300]
            return out

        corrected = resp.corrected_query or ""

        if not len(resp.results):
            # natural-language query (e.g. "a non-stick frying pan for two") -> retry with known keywords
            from .catalog import keyword_terms
            for src in [x for x in ((conv_res or {}).get("refined") or [])[:2]] + [query_for_api]:
                terms = keyword_terms(src, self.local.vocab, limit=5)
                if not terms:
                    continue
                hit = None
                for kq in dict.fromkeys([" ".join(terms[:2])] + terms[:4]):
                    try:
                        r4, f4 = self._search_with_fallback(kq, *args)
                    except Exception as e:  # noqa: BLE001
                        log.warning("keyword retry failed: %s", e)
                        continue
                    if len(r4.results):
                        hit = (r4, f4, kq)
                        break
                if hit:
                    resp, feats, query_for_api = hit
                    break

        ids = [r.id for r in resp.results]
        results = self.hydrate(ids)
        facets_out = self._facets(resp, selected, tile_facets)
        features = {f: ("live" if f in feats else "fallback") for f in ADVANCED}

        # tiles (tileNavigationSpec, else built from facets)
        tiles_out = []
        tnr = getattr(resp, "tile_navigation_result", None)
        if "tile_navigation" in feats and tnr is not None:
            for t in tnr.tiles:
                pav = t.product_attribute_value
                if pav and pav.name and pav.value:
                    key = pav.name if pav.name in ("brands", "categories") or pav.name.startswith("attributes.") else "attributes." + pav.name
                    n = self.catalog.get(t.representative_product_id) if t.representative_product_id else None
                    tiles_out.append({"label": value_label(key, pav.value), "key": key, "value": pav.value,
                                      "filter": f"{key}: ANY({_q(pav.value)})", "image": (n or {}).get("image", "")})
        if not tiles_out:
            tiles_out = tiles_from_facets(facets_out, merge_tiles_into_facets(selected, tiles), self.catalog, results)
            features["tile_navigation"] = "fallback"
            if "tile_navigation" in feats:
                self.status["tile_navigation"] = "fallback"

        # conversation: live :conversationalSearch RPC → in-search spec → facet heuristic
        conv = None
        if conv_res:
            answers = conv_res["suggested"] or [x for x in refined[1:6] if x != query_for_api]
            acts = [{"label": a, "kind": "turn", "value": a} for a in answers]
            question = conv_res["followup_en"]
            conv = {"id": conv_res["conversation_id"] or live_cid,
                    "refined_query": (refined[0] if refined else query_for_api), "refined_queries": refined[:6],
                    "followup_question": question, "followup_question_en": conv_res["followup_en"],
                    "text_response": conv_res["text_en"], "suggested_answers": answers, "answer_actions": acts,
                    "query_types": conv_res["query_types"], "source": "retail"}
            features["conversational"] = "live"
        else:
            csr = getattr(resp, "conversational_search_result", None)
            if "conversational" in feats and csr is not None and (csr.followup_question or csr.suggested_answers):
                acts = []
                for sa in csr.suggested_answers:
                    pav = sa.product_attribute_value
                    if pav.value:
                        key = pav.name if pav.name in ("brands", "categories") or pav.name.startswith("attributes.") else "attributes." + pav.name
                        acts.append({"label": value_label(key, pav.value), "kind": "answer", "key": key, "value": pav.value})
                conv = {"id": csr.conversation_id, "refined_query": csr.refined_query or q,
                        "followup_question": csr.followup_question, "suggested_answers": [a["label"] for a in acts],
                        "answer_actions": acts, "source": "retail"}
                features["conversational"] = "live"
        if conv is None:
            conv = heuristic_conversation(q, facets_out, merge_tiles_into_facets(selected, tiles), live_cid,
                                          source="heuristic")
            features["conversational"] = "live" if (rq or answer_key) and live_cid else "fallback"
        return {
            "results": results,
            "total": int(resp.total_size or len(results)),
            "facets": facets_out,
            "tiles": tiles_out,
            "conversation": conv,
            "conversation_turn": bool(turn),
            "results_query": query_for_api,
            "corrected_query": corrected,
            "applied_filter": filt,
            "engine": "retail",
            "features": features,
        }

    def _facets(self, resp: Any, selected: dict[str, list[str]], tile_facets: dict[str, list[str]]) -> list[dict]:
        out = []
        for f in resp.facets:
            key = f.key
            vals = []
            for v in f.values:
                if v.interval and (v.interval.minimum or v.interval.exclusive_maximum or v.interval.maximum):
                    lo = v.interval.minimum
                    hi = v.interval.exclusive_maximum or v.interval.maximum or None
                    value = price_interval_value(lo, hi)
                else:
                    value = v.value
                if not value:
                    continue
                vals.append({"value": value, "label": value_label(key, value), "count": int(v.count),
                             "selected": value in (selected.get(key) or []) or value in (tile_facets.get(key) or [])})
            if not vals:
                continue
            out.append({"key": key, "label": FACET_LABELS.get(key, key.replace("attributes.", "")),
                        "values": _sort_facet(key, vals)[:12], "dynamic": bool(getattr(f, "dynamic_facet", False))})
        return out

    # ---- autocomplete ----
    def autocomplete(self, q: str, visitor_id: str = "") -> dict:
        try:
            resp = self.completion.complete_query(request={
                "catalog": self.catalog_name, "query": q, "visitor_id": visitor_id or "cymbal-site",
                "language_codes": [os.environ.get("LANGUAGE_CODE", "en")], "max_suggestions": 8}, timeout=5)
            sugg = [r.suggestion for r in resp.completion_results if r.suggestion]
            if sugg:
                self.status["autocomplete"] = "live"
                return {"suggestions": sugg, "source": "retail"}
            self.status["autocomplete"] = self.status.get("autocomplete") if self.status.get("autocomplete") == "live" else "fallback"
        except Exception as e:  # noqa: BLE001
            log.warning("complete_query failed: %s", str(e)[:200])
            self.status["autocomplete"] = "fallback"
        return {"suggestions": self.local.suggestions.match(q) or self.catalog.suggest(q), "source": "local"}
