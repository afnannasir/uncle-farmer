"""Every shipped data/*.json file must match its schema before a run can use it.

The files are hand-built from the game and drive travel, farming, shards and skills. A typo or a
wrong JSON type used to surface only when a bot reached that map at night; these models make it
fail in CI. Unknown keys are rejected so a misspelled field cannot silently do nothing.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError, model_validator

DATA = Path(__file__).resolve().parents[1] / "data"
OPTIONAL_DATA = {"game_texts.json"}

Cell = tuple[int, int]
Rect = tuple[int, int, int, int]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AtlasExit(Strict):
    dest: int | None = None
    marker: int | None = None
    rect: Rect
    side: str
    stand: Cell | None = None
    comp: int


class AtlasArrival(Strict):
    marker: int
    sources: list[int]
    point: Cell | None = None
    comp: int


class AtlasPort(Strict):
    kind: str
    name: str
    x: int
    y: int
    exit: int | None = None
    dest: int | None = None
    marker: int | None = None
    comp: int


class AtlasRoute(Strict):
    a: int
    b: int
    steps: int
    min_clear: int
    tight: list[tuple[str, str, int]]
    ledge_cells: int
    oneway_cells: int
    steps_without_oneway: int | None
    back_steps: int
    gates_ignored: bool
    fwd: list[str]
    back: list[str]


class AtlasZone(Strict):
    kind: str
    marker: int
    label: str
    gloss: str
    level: int | None
    density: str
    rect: Rect
    walk: int
    reachable: int
    area: str | None


class AtlasPatch(Strict):
    cells: int
    rect: Rect
    spot: Cell
    spot_far: int
    spot_clear: int
    comp: int
    reachable: bool
    area: str | None


class AtlasSpawnPort(Strict):
    name: str
    x: int
    y: int
    kind: int


class AtlasSpawn(Strict):
    zones: list[AtlasZone]
    walkable: int
    hostile: int
    levels: list[int]
    has_elite: bool
    has_boss: bool
    patches: list[AtlasPatch]
    patch_count: int
    ports: list[AtlasSpawnPort]


class AtlasMap(Strict):
    id: int
    name: str
    w: int
    h: int
    reading: str
    walkable: int
    wall_file: str
    obstacles: str
    report: str
    exits: list[AtlasExit]
    arrivals: list[AtlasArrival]
    ports: list[AtlasPort]
    routes: list[AtlasRoute]
    areas: list[tuple[str, int, int, int, int]]
    towns: list[tuple[str, int, int, int, int]]
    directional: list[tuple[int, str, int, int, int, int, int]]
    spawn: AtlasSpawn


class AtlasFile(Strict):
    format: str
    maps: dict[str, AtlasMap]

    @model_validator(mode="after")
    def _keys_are_ids(self):
        for key, atlas_map in self.maps.items():
            if key != str(atlas_map.id):
                raise ValueError("map key %r does not match id %d" % (key, atlas_map.id))
        return self


class Beast(Strict):
    kind: str
    speed: float
    level: int
    free: bool
    no_skills: bool
    hp: int
    mp: int
    attack: int
    magic_attack: int
    defense: int
    magic_defense: int
    radiation_proof: bool
    text: str


class BeastsFile(Strict):
    base_speed: float
    beasts: dict[str, Beast]


class DungeonEntrance(Strict):
    name: str
    dungeon_maps: list[int]
    map: int
    npc: str | None
    at: Cell | None
    spots: list[tuple[int, int, str]]
    team: bool | None
    about: str | None = None


class DungeonNotFound(Strict):
    name: str
    maps: list[int]


class DungeonsFile(Strict):
    format: int
    note: str
    entrances: list[DungeonEntrance]
    not_found: list[DungeonNotFound]


class FarmJoin(Strict):
    name: str
    at: Cell
    wps: list[str]


class FarmLeave(Strict):
    name: str
    dest: int
    side: str
    push: str
    wps: list[str]


class FarmVariant(Strict):
    axis: str
    bad_hops: list[list[str]]
    covered: int
    join: list[FarmJoin]
    leave: list[FarmLeave]
    loop: list[str]
    pct: float
    resume: float
    start: Cell
    steps: int
    trips: int


class Farm(Strict):
    id: int
    targets: int
    unreachable: int
    boss_map: bool
    boss: list[tuple[str, Rect]]
    variants: dict[str, FarmVariant] | None = None
    secs: float
    note: str | None = None


class FarmsFile(RootModel[dict[str, Farm]]):
    @model_validator(mode="after")
    def _keys_are_ids(self):
        for key, farm in self.root.items():
            if key != str(farm.id):
                raise ValueError("farm key %r does not match id %d" % (key, farm.id))
        return self


class GameTextsFile(Strict):
    stamp: int
    texts: list[str]


class KnowledgeSources(Strict):
    surveyed_maps: list[int]
    packet_logs: bool
    language_pack: bool
    help_guide: bool


class NpcGood(Strict):
    item: str
    price: int
    currency: str
    they: str
    description: str | None = None


class KnowledgeNpc(Strict):
    map: int
    map_name: str
    name: str
    x: int
    y: int
    role: str
    kind: str
    description: str
    sources: list[str]
    menus_read: str | None = None
    goods: list[NpcGood] | None = None
    walks: list[float] | None = None


class ServiceNpc(Strict):
    name: str
    x: int
    y: int


class KnowledgeCity(Strict):
    city: str
    map: int
    map_name: str
    square: str
    square_cell: Cell
    town_rect: Rect
    services: dict[str, list[ServiceNpc]]


class ItemPrice(Strict):
    npc: str
    map: int
    they: str
    price: int
    currency: str


class KnowledgeItem(Strict):
    name: str
    category: str
    description: str | None = None
    effect: str | None = None
    prices: list[ItemPrice] | None = None


class KnowledgeMap(Strict):
    map: int
    name: str
    levels: list[int]
    has_elite: bool
    has_boss: bool
    towns: list[str]
    walk_exits: int
    surveyed: bool
    drops: list[str] | None = None


class KnowledgeGuide(Strict):
    maps: list[str]
    drops: list[str]


class KnowledgeFile(Strict):
    format: int
    built: str
    sources: KnowledgeSources
    npcs: list[KnowledgeNpc]
    cities: list[KnowledgeCity]
    squares: dict[str, tuple[int, int, int]]
    items: list[KnowledgeItem]
    maps: list[KnowledgeMap]
    guide: KnowledgeGuide


MapKindName = Literal[
    "arena", "dungeon", "dungeon?", "event", "field", "fishing", "gang", "instance",
    "secret_room", "test", "transit", "wedding",
]


class MapKind(Strict):
    name: str
    kind: MapKindName
    why: list[str]
    exits: int
    monsters: bool
    boss: bool
    stage_barriers: int


class MapKindsFile(RootModel[dict[str, MapKind]]):
    pass


class Shard(Strict):
    price: int = Field(ge=0)
    makes: str


class ShardsFile(Strict):
    shards: dict[str, Shard]
    markets: dict[str, tuple[float, float]]


class ShortLoop(Strict):
    covered: float | None
    from_: str | None = Field(default=None, alias="from")
    loop: list[str]
    steps: int
    stops: int | None = None


class ShortLoopsFile(RootModel[dict[str, ShortLoop]]):
    pass


class LevelStats(Strict):
    attack: int | None
    mana: int | None


class Skill(Strict):
    text: str
    range: float | None
    cd: float | None
    cast: float | None
    targets: int | None
    rage_gain: int | None
    rage_cost: int | None
    combo_cost: int | None
    mana: int | None
    mana_pct: int | None
    attack: int | None
    bonus_pct: int | None
    no_gcd: bool
    effects: list[str]
    role: str
    name: str
    order: int
    max_level: int
    by_level: dict[str, LevelStats] | None = None
    when: str | None = None


class Talent(Strict):
    name: str
    order: int
    text: str
    requires: str | None
    excludes: list[str]


class SkillsFile(Strict):
    classes: dict[str, list[Skill]]
    talents: dict[str, list[Talent]]


class TransportLands(Strict):
    map: int
    cell: Cell
    known: bool
    ports: dict[str, int]


class Transport(Strict):
    kind: str
    map: int
    npc: str
    at: Cell
    ride: int | None
    verified: bool | str
    cost: int | str
    note: str
    team: bool
    choose: list[str]
    stand: Cell
    comp: int
    ports: dict[str, int]
    dest: int
    lands: TransportLands
    choose_vip: list[str] | None = None


class TransportsFile(Strict):
    format: int
    transports: list[Transport]


class BossesFile(Strict):
    source: str
    bosses: dict[str, int]


MODELS: dict[str, type[BaseModel]] = {
    "atlas.json": AtlasFile,
    "bosses.json": BossesFile,
    "beasts.json": BeastsFile,
    "dungeons.json": DungeonsFile,
    "farms.json": FarmsFile,
    "game_texts.json": GameTextsFile,
    "knowledge.json": KnowledgeFile,
    "map_kinds.json": MapKindsFile,
    "shards.json": ShardsFile,
    "short_loops.json": ShortLoopsFile,
    "skills.json": SkillsFile,
    "transports.json": TransportsFile,
}


@lru_cache(maxsize=None)
def validated(name: str) -> BaseModel:
    with (DATA / name).open(encoding="utf-8") as f:
        raw = json.load(f)
    return MODELS[name].model_validate(raw)


def model(name: str) -> BaseModel:
    path = DATA / name
    if not path.exists():
        if name in OPTIONAL_DATA:
            pytest.skip("%s is built locally, not shipped" % name)
        pytest.fail("missing data file %s" % name)
    return validated(name)


@pytest.mark.parametrize("name", sorted(MODELS))
def test_data_file_matches_model(name):
    assert model(name) is not None


def test_every_data_json_has_a_model():
    found = {path.name for path in DATA.glob("*.json")}
    assert found <= set(MODELS), "no model for: %s" % sorted(found - set(MODELS))


def test_map_references_exist_in_the_atlas():
    atlas = model("atlas.json")
    assert isinstance(atlas, AtlasFile)
    ids = {atlas_map.id for atlas_map in atlas.maps.values()}

    exits = {atlas_map.id: [e.dest for e in atlas_map.exits if e.dest is not None]
             for atlas_map in atlas.maps.values()}
    assert not {dest for dests in exits.values() for dest in dests} - ids, "atlas exit destination missing"
    assert not {source for atlas_map in atlas.maps.values() for a in atlas_map.arrivals
                for source in a.sources} - ids, "atlas arrival source missing"
    for atlas_map in atlas.maps.values():
        for port in atlas_map.ports:
            if port.exit is not None:
                assert 0 <= port.exit < len(atlas_map.exits), "port exit index out of range"
                assert port.dest == atlas_map.exits[port.exit].dest, "port destination differs from its exit"
            if port.dest is not None:
                assert port.dest in ids, "port destination missing"
        for route in atlas_map.routes:
            assert 0 <= route.a < len(atlas_map.ports), "route start port out of range"
            assert 0 <= route.b < len(atlas_map.ports), "route end port out of range"

    farms = model("farms.json")
    assert isinstance(farms, FarmsFile)
    assert not {farm.id for farm in farms.root.values()} - ids, "farm map missing"

    kinds = model("map_kinds.json")
    assert isinstance(kinds, MapKindsFile)
    assert not {int(key) for key in kinds.root} - ids, "map kind map missing"

    shards = model("shards.json")
    assert isinstance(shards, ShardsFile)
    assert not {int(key) for key in shards.markets} - ids, "shard market map missing"

    transports = model("transports.json")
    assert isinstance(transports, TransportsFile)
    transport_maps = {t.map for t in transports.transports}
    transport_maps |= {t.dest for t in transports.transports}
    transport_maps |= {t.ride for t in transports.transports if t.ride is not None}
    transport_maps |= {t.lands.map for t in transports.transports}
    assert not transport_maps - ids, "transport map missing"

    dungeons = model("dungeons.json")
    assert isinstance(dungeons, DungeonsFile)
    dungeon_maps = {e.map for e in dungeons.entrances}
    dungeon_maps |= {m for e in dungeons.entrances for m in e.dungeon_maps}
    dungeon_maps |= {m for e in dungeons.not_found for m in e.maps}
    assert not dungeon_maps - ids, "dungeon map missing"

    knowledge = model("knowledge.json")
    assert isinstance(knowledge, KnowledgeFile)
    knowledge_maps = {m.map for m in knowledge.maps}
    knowledge_maps |= {n.map for n in knowledge.npcs}
    knowledge_maps |= {c.map for c in knowledge.cities}
    knowledge_maps |= {square[0] for square in knowledge.squares.values()}
    assert not knowledge_maps - ids, "knowledge map missing"


def test_models_reject_malformed_edits():
    with pytest.raises(ValidationError):
        ShardsFile.model_validate({"shards": {}, "markets": {}, "shard": {}})
    with pytest.raises(ValidationError):
        ShardsFile.model_validate({"shards": {"Ruby Shard": {"price": "free", "makes": ""}}, "markets": {}})
