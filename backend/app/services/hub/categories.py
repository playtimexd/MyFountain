"""Library category tree for hub assets.

Every asset gets one category path such as ``audio/sfx/weapons`` (stored in ``assets.category``) and a
``collection`` (the pack or site it came from, e.g. "Game Animation Sample (GASP)" or "Poly Haven").
Categories are derived by rules from kind + pack + name + tags so re-running them is cheap and deterministic;
a category set by hand (``extra.category_manual``) is kept.
"""
from __future__ import annotations

import re

# (path, English label). Order = display order. Parents must precede children.
TREE: list[tuple[str, str]] = [
    ("video", "Video"),
    ("video/stock", "Stock footage"),
    ("video/space", "Space & science"),
    ("video/other", "Other video"),

    ("image", "Images"),
    ("image/photos", "Photos"),
    ("image/space", "Space & science"),
    ("image/art", "Art & illustrations"),
    ("image/other", "Other images"),

    ("audio", "Audio"),
    ("audio/music", "Music"),
    ("audio/sfx", "Sound effects"),
    ("audio/sfx/weapons", "Weapons & combat"),
    ("audio/sfx/foley", "Foley & footsteps"),
    ("audio/sfx/impacts", "Impacts & explosions"),
    ("audio/sfx/ui", "UI & interface"),
    ("audio/sfx/ambience", "Ambience & nature"),
    ("audio/sfx/vehicles", "Vehicles & machines"),
    ("audio/sfx/other", "Other effects"),
    ("audio/voice", "Voice & speech"),

    ("models", "3D models"),
    ("models/characters", "Characters & bodies"),
    ("models/creatures", "Animals & creatures"),
    ("models/weapons", "Weapons & ammo"),
    ("models/furniture", "Furniture & interior"),
    ("models/props", "Props & objects"),
    ("models/nature", "Plants & nature"),
    ("models/buildings", "Buildings & architecture"),
    ("models/vehicles", "Vehicles"),
    ("models/other", "Other models"),

    ("anim", "Animations"),
    ("anim/100style", "100STYLE stylized locomotion"),
    ("anim/gasp", "UE5 Game Animation Sample (GASP)"),
    ("anim/lyra", "UE5 Lyra"),
    ("anim/als", "UE5 Advanced Locomotion (ALS)"),
    ("anim/mannequin", "UE5 Mannequin basics"),
    ("anim/weapons", "Weapon animations"),
    ("anim/mocap", "CMU motion capture"),
    ("anim/other", "Other animations"),

    ("surfaces", "Materials & HDRIs"),
    ("surfaces/materials", "PBR materials"),
    ("surfaces/materials/stone", "Stone & rock"),
    ("surfaces/materials/brick", "Brick, concrete & tiles"),
    ("surfaces/materials/ground", "Ground, soil & sand"),
    ("surfaces/materials/wood", "Wood"),
    ("surfaces/materials/metal", "Metal"),
    ("surfaces/materials/fabric", "Fabric & leather"),
    ("surfaces/materials/other", "Other materials"),
    ("surfaces/textures", "Textures"),
    ("surfaces/hdris", "HDRIs & skies"),
    ("surfaces/hdris/outdoor", "Outdoor"),
    ("surfaces/hdris/skies", "Pure skies"),
    ("surfaces/hdris/indoor", "Indoor & studio"),

    ("icons", "Icons & 2D"),
    ("icons/game", "Game icons"),
    ("icons/game/weapons", "Weapons & armour"),
    ("icons/game/creatures", "Creatures & characters"),
    ("icons/game/body", "Body & faces"),
    ("icons/game/items", "Items, food & potions"),
    ("icons/game/magic", "Magic & effects"),
    ("icons/game/tools", "Tools & tech"),
    ("icons/game/places", "Places & buildings"),
    ("icons/game/vehicles", "Vehicles"),
    ("icons/game/symbols", "Symbols & UI"),
    ("icons/game/misc", "Misc"),
    ("icons/sprites", "Sprites & tiles"),

    ("fonts", "Fonts"),
    ("fonts/sans", "Sans serif"),
    ("fonts/serif", "Serif"),
    ("fonts/display", "Display"),
    ("fonts/handwriting", "Handwriting"),
    ("fonts/mono", "Monospace"),
    ("fonts/other", "Other fonts"),

    ("other", "Other"),
]
LABELS = dict(TREE)
VALID = set(LABELS)


def label(path: str) -> str:
    return LABELS.get(path, path.rsplit("/", 1)[-1].replace("-", " ").title())


def tree(counts: dict[str, int] | None = None) -> list[dict]:
    """Nested [{id,label,count,children}] with counts rolled up to parents (empty branches are kept)."""
    counts = counts or {}
    nodes = {p: {"id": p, "label": l, "count": 0, "own": int(counts.get(p, 0)), "children": []} for p, l in TREE}
    for p, n in counts.items():          # unknown paths still count under their nearest known ancestor
        if p and p not in nodes:
            q = p
            while q and q not in nodes:
                q = q.rpartition("/")[0]
            if q:
                nodes[q]["own"] += int(n)
    for p in reversed([p for p, _ in TREE]):
        nd = nodes[p]
        nd["count"] = nd["own"] + sum(c["count"] for c in nd["children"])
        parent = p.rpartition("/")[0]
        if parent:
            nodes[parent]["children"].insert(0, nd)
    return [nodes[p] for p, _ in TREE if "/" not in p]


# ------------------------------------------------------------------ collections (pack / site)
SOURCE_NAMES = {"polyhaven": "Poly Haven", "ambientcg": "ambientCG", "googlefonts": "Google Fonts", "cmu": "CMU Motion Capture",
                "polypizza": "Poly Pizza", "sketchfab": "Sketchfab", "freesound": "Freesound", "openverse": "Openverse",
                "nasa": "NASA Image and Video Library", "kenney": "Kenney", "quaternius": "Quaternius", "mixamo": "Mixamo",
                "truebones": "Truebones"}


def collection(rec: dict) -> str:
    extra = rec.get("extra") or {}
    pack = str(extra.get("pack") or "").strip()
    if pack:
        if pack.lower().startswith("game-icons.net"):
            return "game-icons.net"
        pack = re.sub(r"\s+-\s+(animation|sound|static|skeletal)$", "", pack, flags=re.I)
        return pack
    prov = extra.get("provider")
    if rec.get("source") == "openverse" and prov:
        return f"Openverse · {prov}"
    return SOURCE_NAMES.get(rec.get("source") or "", rec.get("source") or "")


# ------------------------------------------------------------------ rules
def _words(*parts) -> str:
    out = []
    for p in parts:
        if isinstance(p, (list, tuple, set)):
            out.extend(str(x) for x in p)
        elif p:
            out.append(str(p))
    s = " ".join(out)
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)          # CamelCase -> words
    return " " + re.sub(r"[^a-z0-9]+", " ", s.lower()) + " "


def _has(text: str, *keys: str) -> bool:
    return any(f" {k}" in text for k in keys)       # prefix match on word starts


def _has_word(text: str, *keys: str) -> bool:
    """Whole word (or simple plural) match: 'ear' hits 'ear'/'ears', not 'earth'."""
    toks = text.split()
    for k in keys:
        if " " in k:
            if f" {k}" in text:
                return True
            continue
        for t in toks:
            if t == k or t == k + "s" or t == k + "es" or (len(k) >= 3 and (t.endswith(k) or t.endswith(k + "s"))):
                return True   # endswith catches compounds such as 'broadsword', 'foxhead' is not needed
    return False


def _first(text: str, rules: list[tuple[str, tuple[str, ...]]], default: str, words: bool = False) -> str:
    test = _has_word if words else _has
    for cat, keys in rules:
        if test(text, *keys):
            return cat
    return default


SFX_RULES = [
    ("audio/sfx/weapons", ("rifle", "pistol", "shotgun", "gun ", "gunshot", "grenade", "rocket", "sniper", "reload", "bullet", "sword",
                           "blade", "knife", "weapon", "military", "laser", "arrow", "bow ")),
    ("audio/sfx/foley", ("foley", "footstep", "foot step", "fs ", "sneaker", "walk", "run ", "jump", "land", "scuff", "cloth", "body ")),
    ("audio/sfx/impacts", ("impact", "hit", "punch", "explosion", "explode", "blast", "crash", "smash", "boom", "thud", "break")),
    ("audio/sfx/ui", ("ui ", "click", "button", "interface", "menu", "notification", "beep", "blip", "select", "confirm", "error")),
    ("audio/sfx/ambience", ("ambience", "ambient", "atmosphere", "nature", "forest", "rain", "wind", "water", "ocean", "bird", "city",
                            "crowd", "room tone", "field recording", "thunder", "fire")),
    ("audio/sfx/vehicles", ("car ", "engine", "vehicle", "motor", "train", "plane", "aircraft", "machine", "helicopter", "truck")),
]
MODEL_RULES = [
    ("models/weapons", ("rifle", "pistol", "shotgun", "grenade", "rocket launcher", "sniper", "knife", "sword", "weapon", "ammo",
                        "gun", "axe", "bow ", "spear", "shield")),
    ("models/characters", ("character", "mannequin", "manny", "quinn", "skm ", "body ", "head ", "skull", "skeleton", "hand ",
                           "foot ", "eye ", "jaw ", "human", "man ", "woman", "figure", "robot", "humanoid")),
    ("models/creatures", ("animal", "fox", "bear", "dog", "cat ", "horse", "bird", "fish", "creature", "monster", "dragon", "quadruped")),
    ("models/vehicles", ("car ", "vehicle", "truck", "bike", "boat", "ship", "plane", "aircraft", "tank ", "train", "cart ")),
    ("models/furniture", ("furniture", "chair", "table", "sofa", "couch", "bed ", "shelf", "cabinet", "desk", "stool", "bench",
                          "seating", "lamp", "lighting", "drawer", "wardrobe", "dresser")),
    ("models/nature", ("plant", "tree", "shrub", "bush", "flower", "grass", "rock", "boulder", "stone", "foliage", "botany", "fern",
                       "leaf", "nature", "log ", "stump", "mushroom", "ground cover")),
    ("models/buildings", ("building", "house", "architecture", "wall", "door", "window", "roof", "fence", "gate", "column",
                          "stairs", "bridge", "modular")),
]
MAT_RULES = [
    ("surfaces/materials/wood", ("wood", "plank", "bark", "parquet", "timber")),
    ("surfaces/materials/metal", ("metal", "rust", "steel", "iron", "copper", "aluminium", "aluminum", "chrome", "corrugated")),
    ("surfaces/materials/fabric", ("fabric", "leather", "cloth", "carpet", "textile", "denim", "wool", "rug")),
    ("surfaces/materials/brick", ("brick", "concrete", "plaster", "tile", "wall", "asphalt", "pavement", "paving", "cobble")),
    ("surfaces/materials/ground", ("ground", "dirt", "soil", "sand", "gravel", "mud", "grass", "moss", "snow", "forest floor",
                                   "leaves", "terrain")),
    ("surfaces/materials/stone", ("stone", "rock", "marble", "granite", "slate", "cliff", "pebble")),
]
ICON_RULES = [
    ("icons/game/weapons", ("sword", "axe", "dagger", "spear", "bow", "arrow", "gun", "pistol", "rifle", "shield", "armor", "armour",
                            "helmet", "mace", "hammer", "blade", "knife", "crossbow", "halberd", "katana", "bomb", "grenade",
                            "cannon", "missile", "trident", "whip", "flail", "scythe", "lance", "rapier", "saber", "sabre",
                            "musket", "revolver", "breastplate", "gauntlet", "chainmail", "bullet", "ammo", "quiver", "helm", "shuriken", "barbute", "bolas", "boomerang", "dynamite", "explosive", "turret", "laser", "barbed", "claymore", "nunchaku", "trebuchet", "catapult", "ballista", "mine", "shot", "slashes", "crossed swords")),
    ("icons/game/creatures", ("dragon", "monster", "wolf", "bear", "fox", "cat", "dog", "bird", "fish", "snake", "spider",
                              "insect", "bug", "beetle", "skeleton", "zombie", "ghost", "demon", "orc", "goblin", "troll", "elf",
                              "dwarf", "knight", "wizard", "witch", "warrior", "ninja", "pirate", "robot", "alien", "horse",
                              "lion", "tiger", "owl", "eagle", "crow", "raven", "bat", "rat", "frog", "turtle", "shark",
                              "whale", "octopus", "squid", "crab", "unicorn", "griffin", "minotaur", "kraken", "creature",
                              "beast", "monk", "king", "queen", "princess", "soldier", "archer", "person", "man", "woman",
                              "character", "cowboy", "viking", "samurai", "angel", "devil", "imp", "slime", "werewolf",
                              "vampire", "mummy", "cyclops", "golem", "pig", "cow", "sheep", "goat", "chicken", "rabbit",
                              "deer", "boar", "ant", "bee", "butterfly", "scorpion", "worm", "jellyfish", "seahorse",
                              "dinosaur", "mammoth", "gorilla", "monkey", "elephant", "rhino", "hippo", "giraffe", "camel", "claw", "claws", "paw", "fang", "fangs", "wing", "wings", "tail", "duck", "goose", "bull", "ram", "cobra", "reptile", "armadillo", "minion", "fairy", "sphinx", "meeple", "player", "team", "fossil", "horned", "hive", "shell", "scales", "seal", "crane", "lizard", "mantis", "dragonfly", "capybara", "feline", "canine", "swordwoman", "swordman")),
    ("icons/game/vehicles", ("car", "truck", "tank", "ship", "boat", "spaceship", "rocket", "plane", "airplane", "jet",
                             "helicopter", "train", "bicycle", "bike", "motorcycle", "cart", "wagon", "carriage", "submarine",
                             "sail", "canoe", "raft", "ufo", "bus", "tractor", "sled", "chariot", "zeppelin", "balloon", "glider", "jeep", "scooter", "buoy", "shuttlecock", "skateboard", "delivery")),
    ("icons/game/items", ("coin", "gem", "jewel", "ring", "necklace", "amulet", "crown", "key", "chest", "treasure", "bag",
                          "backpack", "scroll", "book", "potion", "bottle", "flask", "food", "bread", "meat", "apple", "fruit",
                          "cheese", "cake", "drink", "cup", "mug", "wine", "beer", "pie", "egg", "fish", "mushroom", "carrot",
                          "candle", "lantern", "torch", "map", "letter", "card", "dice", "die", "trophy", "medal", "gift",
                          "box", "barrel", "crate", "basket", "pouch", "purse", "wallet", "money", "gold", "diamond",
                          "herb", "leaf", "flower", "seed", "berry", "pill", "bandage", "syringe", "toy", "ball", "hat",
                          "boot", "shoe", "glove", "cloak", "robe", "shirt", "dress", "belt", "mask", "glasses", "banana", "jar", "pot", "pottery", "vase", "soap", "can", "jacket", "suit", "cap", "hood", "collar", "ribbon", "spoon", "fork", "pizza", "noodles", "kebab", "beans", "coffee", "lemon", "corn", "honey", "butter", "meal", "tarot", "clubs", "spades", "hearts", "sesame", "nachos", "jerrycan", "paper", "tape", "stapler", "table", "glass", "tablet", "capsule", "medical", "health", "thermometer", "wool", "clothes", "vest", "shirt", "jelly")),
    ("icons/game/tools", ("tool", "wrench", "pickaxe", "shovel", "saw", "drill", "gear", "cog", "screw", "anvil", "scissors",
                          "needle", "rope", "chain", "lock", "trap", "computer", "phone", "radio", "battery", "circuit",
                          "chip", "laptop", "camera", "microscope", "telescope", "compass", "clock", "hourglass", "magnet",
                          "lamp", "light bulb", "lightbulb", "robot arm", "satellite", "antenna", "server", "keyboard",
                          "mouse", "joystick", "gamepad", "plug", "pipe", "valve", "hook", "ladder", "bucket", "broom",
                          "brush", "pen", "pencil", "quill", "paint", "palette", "test tube", "flask", "beaker", "vial",
                          "instrument", "guitar", "drum", "horn", "trumpet", "violin", "piano", "harp", "flute", "bell", "coil", "tesla", "machine", "pump", "tire", "wheel", "projector", "console", "cpu", "processor", "atom", "molecule", "cutter", "gavel", "abacus", "spanner", "sink", "solar", "power", "electrical", "charging", "signal", "film", "traffic", "kitchen", "crafting", "sewing", "stick", "thermometer", "histogram")),
    ("icons/game/magic", ("magic", "spell", "fire", "flame", "lightning", "thunder", "ice", "frost", "potion", "rune", "crystal",
                          "orb", "aura", "beam", "burst", "explosion", "star", "sun", "moon", "portal", "summon", "curse",
                          "poison", "acid", "smoke", "tornado", "whirlwind", "vortex", "spark", "energy", "glow", "holy",
                          "pentagram", "tentacle", "blood", "splash", "wave", "meteor", "comet", "bolt", "light", "lights", "eclipse", "shadow", "evil", "death", "sonic", "gooey", "dripping", "drop", "blast", "burning", "steam", "fog", "wind", "tide", "water", "life", "love", "idea", "vision", "dead", "impact", "trefoil", "clover", "zigzag")),
    ("icons/game/places", ("castle", "tower", "house", "village", "city", "temple", "church", "ruins", "cave", "mountain",
                           "forest", "tree", "island", "volcano", "desert", "dungeon", "gate", "door", "bridge", "wall",
                           "fort", "camp", "tent", "hut", "cabin", "factory", "mill", "well", "fountain", "statue",
                           "pyramid", "lighthouse", "windmill", "barn", "shop", "inn", "tavern", "throne", "planet",
                           "earth", "globe", "world", "landscape", "beach", "river", "lake", "sea", "ocean", "cloud", "stone", "brick", "fence", "stairs", "path", "road", "street", "field", "platform", "podium", "garage", "barracks", "dome", "pillar", "column", "entrance", "floor", "ceiling", "cactus", "palm", "sprout", "grass", "log", "boulder", "wheat", "grain", "tombstone", "grave", "africa", "south", "north", "hole", "cage", "bank", "hatch", "level")),
    ("icons/game/body", ("head", "face", "skull", "eye", "hand", "fist", "foot", "leg", "arm", "brain", "heart", "lung",
                         "tooth", "teeth", "mouth", "nose", "ear", "bone", "muscle", "body", "torso", "finger", "beard",
                         "hair", "lips", "organ", "stomach", "spine", "rib", "wound", "heel", "knee", "profile", "ribcage", "headshot", "punch", "kick", "slap", "muscle", "grasping", "eating", "spit")),
    ("icons/game/symbols", ("arrow", "check", "cross", "plus", "minus", "circle", "square", "triangle", "hexagon", "pentagon",
                            "symbol", "sign", "flag", "banner", "badge", "emblem", "icon", "button", "cursor", "target",
                            "infinity", "yin", "peace", "question", "exclamation", "pause", "play", "stop", "speaker",
                            "volume", "save", "settings", "menu", "up", "down", "left", "right", "dots", "pointer",
                            "letter", "number", "alphabet", "abstract", "knot", "spiral", "pattern", "radial", "ring")),
]
FONT_RULES = [
    ("fonts/mono", ("mono", "code", "console", "courier")),
    ("fonts/handwriting", ("handwriting", "script", "hand", "brush", "cursive", "caveat", "dancing", "pacifico", "satisfy")),
    ("fonts/serif", ("serif ", "slab", "garamond", "playfair", "merriweather", "lora", "baskerville", "bodoni", "roman")),
    ("fonts/display", ("display", "pixel", "decorative", "bebas", "lobster", "bungee", "creepster", "press start", "black ops",
                       "anton", "righteous", "monoton")),
    ("fonts/sans", ("sans", "inter", "roboto", "open sans", "lato", "montserrat", "poppins", "noto", "source", "work",
                    "nunito", "raleway", "ubuntu", "oswald", "fira", "rubik", "manrope", "dm ", "outfit", "karla", "barlow")),
]


def _anim(rec: dict, text: str) -> str:
    pack = str((rec.get("extra") or {}).get("pack") or "").lower()
    src = rec.get("source")
    if src == "cmu":
        return "anim/mocap"
    if "100style" in pack:
        return "anim/100style"
    if "gasp" in pack or "game animation sample" in pack:
        return "anim/gasp"
    if "lyra" in pack:
        return "anim/lyra"
    if "als" in pack.split() or "advanced locomotion" in pack:
        return "anim/als"
    if "weapon" in pack:
        return "anim/weapons"
    if "mannequin" in pack:
        return "anim/mannequin"
    if _has(text, "mocap", "motion capture"):
        return "anim/mocap"
    return "anim/other"


def categorize(rec: dict) -> str:
    """Category path for a hub record (dict with kind, name, tags, source, extra, stats, description)."""
    kind = rec.get("kind") or ""
    extra = rec.get("extra") or {}
    stats = rec.get("stats") or {}
    auto = set(stats.get("auto_tags") or [])
    tags = [t for t in (rec.get("tags") or []) if t not in auto]   # CLIP guesses never decide a category
    pack = str(extra.get("pack") or "")
    text = _words(rec.get("name"), tags, pack, extra.get("relpath"), extra.get("category"))
    src = rec.get("source") or ""

    if kind == "animation":
        return _anim(rec, text)
    if kind in ("sfx", "music", "voice"):
        if kind == "music" or _has(text, "music", "song", "jingle", "soundtrack", "loop music", "melody"):
            return "audio/music"
        if kind == "voice" or _has(text, "voice", "speech", "vocal", "announcer", "dialog", "spoken"):
            return "audio/voice"
        return (_first(_words(rec.get("name"), extra.get("relpath")), SFX_RULES, "")
                or _first(text, SFX_RULES, "audio/sfx/other"))
    if kind == "hdri":
        if _has(text, "indoor", "studio", "interior", "artificial light"):
            return "surfaces/hdris/indoor"
        if _has(text, "pure skies", "pure sky"):
            return "surfaces/hdris/skies"
        return "surfaces/hdris/outdoor"
    if kind == "material":
        return _first(_words(rec.get("name")), MAT_RULES, "") or _first(text, MAT_RULES, "surfaces/materials/other")
    if kind == "texture":
        return "surfaces/textures"
    if kind == "font":
        cat = str(extra.get("category") or "").lower().replace("_", " ")
        if not cat:
            cat = next((t for t in tags if t in ("sans serif", "serif", "display", "handwriting", "monospace")), "")
        for k, p in (("monospace", "fonts/mono"), ("handwriting", "fonts/handwriting"), ("display", "fonts/display"),
                     ("sans", "fonts/sans"), ("serif", "fonts/serif")):
            if k in cat:
                return p
        return _first(text, FONT_RULES, "fonts/other")
    if kind == "sprite":
        if "game-icons" in pack.lower() or "game-icons" in tags or _has(text, "icon"):
            return _first(_words(rec.get("name")), ICON_RULES, "icons/game/misc", words=True)
        return "icons/sprites"
    if kind in ("model", "prefab"):
        if bool(stats.get("rigged")) and _has(text, "character", "humanoid", "mannequin", "figure", "man", "robot"):
            return "models/characters"
        name_text = _words(rec.get("name"), extra.get("relpath"))
        hit = _first(name_text, MODEL_RULES, "")
        return hit or _first(text, MODEL_RULES, "models/props" if src == "polyhaven" else "models/other")
    if kind == "video":
        if src == "nasa" or _has(text, "nasa", "space", "astronaut", "rocket", "planet", "galaxy"):
            return "video/space"
        return "video/stock" if src in ("openverse", "pexels", "pixabay", "wikimedia") else "video/other"
    if kind == "image":
        if src == "nasa" or _has(text, "nasa", "galaxy", "nebula", "astronaut"):
            return "image/space"
        cat = str(extra.get("category") or "").lower()
        if cat in ("photograph",) or _has(text, "photo", "photograph"):
            return "image/photos"
        if cat in ("illustration", "digitized_artwork") or _has(text, "painting", "illustration", "drawing", "artwork", "art"):
            return "image/art"
        return "image/photos" if src == "openverse" else "image/other"
    return "other"
