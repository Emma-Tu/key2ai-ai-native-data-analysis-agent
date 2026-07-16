#!/usr/bin/env python3
"""
Proto -> semantic-layer YAML skeleton generator.

Parses the car_hire proto files and emits one YAML file per top-level message
and per enum, capturing everything a parser CAN know for sure:
  - fields (name, proto type, repeated/map, field number, comment)
  - nested messages / nested enums
  - oneof groups
  - enum values + comments
  - external type references (commons.*, clients.*, ...)

What it deliberately leaves as TODO (a parser cannot know these):
  - business meaning beyond the inline comment
  - enum-value semantics beyond the inline comment
  - cross-event relationships / join keys  -> curated in relationships.yaml
  - metric definitions                     -> curated in metrics.yaml

Usage:
  python3 generate_yaml.py            # writes generated/ + messages/ + enums/
Stdlib only. No protoc required.
"""

import os
import re
import glob
import json

PROTO_DIR = os.path.join(os.path.dirname(__file__), "proto")
OUT_MSG = os.path.join(os.path.dirname(__file__), "messages")
OUT_ENUM = os.path.join(os.path.dirname(__file__), "enums")
OUT_GEN = os.path.join(os.path.dirname(__file__), "generated")

SCALARS = {
    "double", "float", "int32", "int64", "uint32", "uint64", "sint32", "sint64",
    "fixed32", "fixed64", "sfixed32", "sfixed64", "bool", "string", "bytes",
}

# ---- tokenizer: strip block comments, keep line comments attached ----

def strip_block_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def yaml_quote(s):
    if s is None:
        return '""'
    s = str(s).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


class Field:
    def __init__(self, name, ptype, number, label, comment, is_map=False,
                 map_kv=None, oneof=None):
        self.name = name
        self.ptype = ptype        # e.g. "commons.DateTime", "string", "Quote"
        self.number = number
        self.label = label        # "", "repeated"
        self.comment = comment
        self.is_map = is_map
        self.map_kv = map_kv      # (key_type, value_type)
        self.oneof = oneof


class EnumDef:
    def __init__(self, name, package, file):
        self.name = name
        self.package = package
        self.file = file
        self.values = []          # list of (name, number, comment)


class MessageDef:
    def __init__(self, name, package, file, parent=None):
        self.name = name
        self.package = package
        self.file = file
        self.parent = parent      # qualified parent name for nested msgs
        self.fields = []
        self.nested_messages = []
        self.nested_enums = []
        self.comment = ""

    @property
    def qualified(self):
        return f"{self.parent}.{self.name}" if self.parent else self.name


def parse_proto(path):
    """Very small proto3 parser: messages, enums, fields, oneof, nested."""
    with open(path) as f:
        raw = f.read()
    raw = strip_block_comments(raw)
    lines = raw.split("\n")

    pkg = ""
    for ln in lines:
        m = re.match(r"\s*package\s+([A-Za-z0-9_.]+)\s*;", ln)
        if m:
            pkg = m.group(1)
            break

    fname = os.path.basename(path)
    messages = {}
    enums = {}

    # stack of ("message"/"enum"/"oneof", object, name)
    stack = []
    pending_comment = []  # comments seen just before a declaration

    def cur_msg():
        for kind, obj, _ in reversed(stack):
            if kind == "message":
                return obj
        return None

    def cur_oneof():
        if stack and stack[-1][0] == "oneof":
            return stack[-1][2]
        return None

    i = 0
    while i < len(lines):
        line = lines[i]
        code, _, linecomment = line.partition("//")
        code = code.strip()
        linecomment = linecomment.strip()

        if not code:
            if linecomment:
                pending_comment.append(linecomment)
            i += 1
            continue

        # message declaration
        m = re.match(r"message\s+([A-Za-z0-9_]+)\s*\{", code)
        if m:
            parent = cur_msg().qualified if cur_msg() else None
            md = MessageDef(m.group(1), pkg, fname, parent)
            md.comment = " ".join(pending_comment)
            pending_comment = []
            if cur_msg():
                cur_msg().nested_messages.append(md)
            else:
                messages[md.name] = md
            stack.append(("message", md, md.name))
            i += 1
            continue

        # enum declaration
        m = re.match(r"enum\s+([A-Za-z0-9_]+)\s*\{", code)
        if m:
            parent = cur_msg().qualified if cur_msg() else None
            ed = EnumDef(m.group(1), pkg, fname)
            ed.parent = parent
            ed.comment = " ".join(pending_comment)
            pending_comment = []
            if cur_msg():
                cur_msg().nested_enums.append(ed)
            else:
                enums[ed.name] = ed
            stack.append(("enum", ed, ed.name))
            i += 1
            continue

        # oneof declaration
        m = re.match(r"oneof\s+([A-Za-z0-9_]+)\s*\{", code)
        if m:
            stack.append(("oneof", None, m.group(1)))
            pending_comment = []
            i += 1
            continue

        # closing brace
        if code.startswith("}"):
            if stack:
                stack.pop()
            pending_comment = []
            i += 1
            continue

        # inside enum: VALUE = n;
        if stack and stack[-1][0] == "enum":
            m = re.match(r"([A-Za-z0-9_]+)\s*=\s*(-?\d+)\s*(\[[^\]]*\])?\s*;", code)
            if m:
                ed = stack[-1][1]
                ed.values.append((m.group(1), int(m.group(2)),
                                  linecomment, bool(m.group(3))))
            pending_comment = []
            i += 1
            continue

        # inside message: field
        md = cur_msg()
        if md is not None:
            # map<k,v> name = n;
            mm = re.match(r"map<\s*([A-Za-z0-9_.]+)\s*,\s*([A-Za-z0-9_.]+)\s*>\s+([A-Za-z0-9_]+)\s*=\s*(\d+)", code)
            if mm:
                md.fields.append(Field(mm.group(3), "map", int(mm.group(4)), "",
                                       linecomment, is_map=True,
                                       map_kv=(mm.group(1), mm.group(2)),
                                       oneof=cur_oneof()))
                pending_comment = []
                i += 1
                continue
            # [repeated] Type name = n;
            fm = re.match(r"(repeated\s+)?([A-Za-z0-9_.]+)\s+([A-Za-z0-9_]+)\s*=\s*(\d+)", code)
            if fm:
                label = "repeated" if fm.group(1) else ""
                md.fields.append(Field(fm.group(3), fm.group(2), int(fm.group(4)),
                                       label, linecomment, oneof=cur_oneof()))
                pending_comment = []
                i += 1
                continue

        pending_comment = []
        i += 1

    return pkg, messages, enums


def type_kind(ptype, local_names):
    """Classify a proto type for annotation purposes."""
    if ptype in SCALARS:
        return "scalar"
    if "." in ptype:               # e.g. commons.DateTime
        return "external"
    if ptype in local_names:
        return "message_or_enum"
    return "message_or_enum"       # nested / same-file


def collect_all(messages):
    """Flatten nested messages/enums to gather all local type names."""
    names = set()

    def walk(md):
        names.add(md.name)
        names.add(md.qualified)
        for nm in md.nested_messages:
            walk(nm)
        for ne in md.nested_enums:
            names.add(ne.name)

    for md in messages.values():
        walk(md)
    return names


def emit_field_yaml(fld, local_names, indent="  "):
    out = []
    out.append(f"{indent}- name: {fld.name}")
    if fld.is_map:
        k, v = fld.map_kv
        out.append(f"{indent}  type: map<{k},{v}>")
    else:
        t = fld.ptype
        if fld.label == "repeated":
            out.append(f"{indent}  type: repeated {t}")
        else:
            out.append(f"{indent}  type: {t}")
    out.append(f"{indent}  number: {fld.number}")
    if fld.oneof:
        out.append(f"{indent}  oneof: {fld.oneof}")
    kind = type_kind(fld.ptype, local_names) if not fld.is_map else "scalar"
    if kind == "external":
        out.append(f"{indent}  ref: {fld.ptype}   # 外部类型，见 generated/external_refs.yaml")
    elif kind == "message_or_enum":
        out.append(f"{indent}  ref: {fld.ptype}")
    # desc: use inline comment if present, else TODO
    if fld.comment:
        out.append(f"{indent}  desc: {yaml_quote(fld.comment)}")
    else:
        out.append(f"{indent}  desc: \"\"   # TODO 待补充业务含义")
    return "\n".join(out)


def emit_message_yaml(md, local_names, group="core", overlay=None):
    overlay = overlay or {}
    ov_desc = overlay.get("description")
    ov_grain = overlay.get("grain")
    lines = []
    lines.append(f"# AUTO-GENERATED skeleton from proto/{md.file}. Enrich desc / relationships by hand.")
    lines.append(f"message: {md.name}")
    lines.append(f"package: {md.package}")
    lines.append(f"source_file: {md.file}")
    lines.append(f"group: {group}")
    # description: overlay (curated) > proto comment > TODO
    if ov_desc:
        lines.append(f"description: {yaml_quote(ov_desc)}   # curated (overlay.yaml)")
    elif md.comment:
        lines.append(f"description: {yaml_quote(md.comment)}")
    else:
        lines.append('description: ""   # TODO 一句话说明这张表代表什么')
    if ov_grain:
        lines.append(f"grain: {yaml_quote(ov_grain)}   # curated (overlay.yaml)")
    else:
        lines.append('grain: ""        # TODO 一行代表什么')
    lines.append("domain: car_hire")
    lines.append("")
    lines.append("fields:")
    for fld in md.fields:
        lines.append(emit_field_yaml(fld, local_names))

    # nested enums
    if md.nested_enums:
        lines.append("")
        lines.append("nested_enums:")
        for ne in md.nested_enums:
            lines.append(f"  - name: {ne.name}")
            lines.append(f"    values:")
            for (vn, num, cmt, dep) in ne.values:
                c = f"   # {cmt}" if cmt else ""
                d = " [deprecated]" if dep else ""
                lines.append(f"      {num}: {vn}{d}{c}")

    # nested messages -> list, they each get their own detail below
    if md.nested_messages:
        lines.append("")
        lines.append("nested_messages:")
        for nm in md.nested_messages:
            lines.append(f"  - name: {nm.name}")
            lines.append(f"    fields:")
            for fld in nm.fields:
                lines.append(emit_field_yaml(fld, local_names, indent="      "))

    return "\n".join(lines) + "\n"


def emit_enum_yaml(ed, group="core", overlay=None):
    overlay = overlay or {}
    ov_desc = overlay.get("desc")
    lines = []
    lines.append(f"# AUTO-GENERATED from proto/{ed.file}.")
    lines.append(f"enum: {ed.name}")
    lines.append(f"package: {ed.package}")
    lines.append(f"source_file: {ed.file}")
    lines.append(f"group: {group}")
    if ov_desc:
        lines.append(f"desc: {yaml_quote(ov_desc)}   # curated (overlay.yaml)")
    elif ed.comment:
        lines.append(f"desc: {yaml_quote(ed.comment)}")
    else:
        lines.append('desc: ""   # TODO')
    lines.append("values:")
    for (vn, num, cmt, dep) in ed.values:
        lines.append(f"  - num: {num}")
        lines.append(f"    name: {vn}")
        meaning = cmt if cmt else ""
        if meaning:
            lines.append(f"    meaning: {yaml_quote(meaning)}")
        else:
            lines.append(f"    meaning: \"\"   # TODO 待确认业务含义")
        if dep:
            lines.append(f"    deprecated: true")
    return "\n".join(lines) + "\n"


def load_overlay():
    """Load curated descriptions that survive regeneration.

    overlay.yaml format (hand-maintained, NOT generated):
      messages:
        car_hire.CarGroupCardEvent:
          description: "..."
          grain: "..."
      enums:
        car_hire.PayType:
          desc: "..."
    Minimal parser (no PyYAML): only handles this two-level structure with
    scalar `description`/`grain`/`desc` string values (quoted).
    """
    path = os.path.join(os.path.dirname(__file__), "overlay.yaml")
    ov = {"messages": {}, "enums": {}}
    if not os.path.exists(path):
        return ov
    section = None
    cur_key = None
    for raw in open(path):
        line = raw.rstrip("\n")
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s in ("messages:", "enums:"):
            section = s[:-1]
            cur_key = None
            continue
        if section is None:
            continue
        indent = len(line) - len(line.lstrip())
        # type key: 2-space indent, "pkg.Name:"
        m = re.match(r"([A-Za-z0-9_]+\.[A-Za-z0-9_]+):\s*$", s)
        if indent == 2 and m:
            cur_key = m.group(1)
            ov[section][cur_key] = {}
            continue
        # attribute: 4-space indent
        am = re.match(r"(description|grain|desc):\s*(.*)$", s)
        if cur_key and indent >= 4 and am:
            val = am.group(2).strip()
            if val.startswith('"') and val.endswith('"') and len(val) > 1:
                val = val[1:-1]
            ov[section][cur_key][am.group(1)] = val
    return ov


def load_scope():
    """Minimal reader for scope.yaml's specific structure. No PyYAML needed."""
    path = os.path.join(os.path.dirname(__file__), "scope.yaml")
    if not os.path.exists(path):
        return None
    protos = {}
    cur = None
    in_protos = False
    for raw in open(path):
        line = raw.rstrip("\n")
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s == "protos:":
            in_protos = True
            continue
        if not in_protos:
            continue
        indent = len(line) - len(line.lstrip())
        # proto entry (2-space indent), key ends with .proto
        m = re.match(r"([A-Za-z0-9_./]+\.proto):\s*(.*)$", s)
        if indent <= 2 and m:
            name, rest = m.group(1), m.group(2).strip()
            cur = {"mode": "full", "group": "core"}
            protos[name] = cur
            if rest.startswith("{"):        # inline dict
                body = rest.strip("{} ")
                for pair in body.split(","):
                    if ":" in pair:
                        k, v = pair.split(":", 1)
                        cur[k.strip()] = v.strip()
            continue
        # sub-keys (4-space indent) of current proto
        if cur is not None and indent >= 4 and ":" in s:
            k, v = s.split(":", 1)
            k, v = k.strip(), v.strip()
            if v.startswith("[") and v.endswith("]"):
                cur[k] = [x.strip() for x in v.strip("[]").split(",") if x.strip()]
            else:
                cur[k] = v
    return protos


def enums_used_by(md, all_enum_names):
    """Enum type names referenced by a message's fields (for subset mode)."""
    used = set()
    def walk(m):
        for fld in m.fields:
            base = fld.ptype.split(".")[-1]
            if base in all_enum_names:
                used.add(base)
        for nm in m.nested_messages:
            walk(nm)
    walk(md)
    return used


def main():
    all_messages = {}
    all_enums = {}
    file_index = {}
    scope = load_scope()
    overlay = load_overlay()

    for path in sorted(glob.glob(os.path.join(PROTO_DIR, "*.proto"))):
        pkg, msgs, enums = parse_proto(path)
        fname = os.path.basename(path)
        file_index[fname] = {"package": pkg,
                             "messages": sorted(msgs.keys()),
                             "enums": sorted(enums.keys())}
        for k, v in msgs.items():
            all_messages[f"{pkg}::{k}"] = v
        for k, v in enums.items():
            all_enums[f"{pkg}::{k}"] = v

    local_names = set()
    for v in all_messages.values():
        local_names |= collect_all({v.name: v})
    for k in all_enums.values():
        local_names.add(k.name)
    all_enum_names = {e.name for e in all_enums.values()}

    def group_of(fname):
        if scope and fname in scope:
            return scope[fname].get("group", "core")
        return "core"

    def wanted(md_or_ed, is_enum=False):
        """Decide whether to emit this type given scope config."""
        fname = md_or_ed.file
        if not scope or fname not in scope:
            return True                       # no scope entry -> emit (back-compat)
        cfg = scope[fname]
        mode = cfg.get("mode", "full")
        if mode == "full":
            return True
        if mode == "messages_only":
            if is_enum:
                return False                  # only listed messages (+ their nested enums)
            return md_or_ed.name in cfg.get("messages", [])
        if mode == "subset":
            listed = set(cfg.get("types", []))
            if md_or_ed.name in listed:
                return True
            if is_enum:
                # include enums used by any listed message in this file
                for m in all_messages.values():
                    if m.file == fname and m.name in listed:
                        if md_or_ed.name in enums_used_by(m, all_enum_names):
                            return True
                return False
            return False
        return True

    os.makedirs(OUT_MSG, exist_ok=True)
    os.makedirs(OUT_ENUM, exist_ok=True)
    os.makedirs(OUT_GEN, exist_ok=True)

    ext_refs = {}
    def scan_ext(md):
        for fld in md.fields:
            if "." in fld.ptype and not fld.is_map:
                ext_refs.setdefault(fld.ptype, []).append(f"{md.file}:{md.qualified}.{fld.name}")
        for nm in md.nested_messages:
            scan_ext(nm)
    for v in all_messages.values():
        scan_ext(v)

    written_msg = 0
    group_counts = {}
    for key, md in all_messages.items():
        if not wanted(md, is_enum=False):
            continue
        g = group_of(md.file)
        ov = overlay["messages"].get(f"{md.package}.{md.name}")
        out = emit_message_yaml(md, local_names, group=g, overlay=ov)
        with open(os.path.join(OUT_MSG, f"{md.package}.{md.name}.yaml"), "w") as f:
            f.write(out)
        written_msg += 1
        group_counts[g] = group_counts.get(g, 0) + 1

    written_enum = 0
    for key, ed in all_enums.items():
        if not wanted(ed, is_enum=True):
            continue
        g = group_of(ed.file)
        ov = overlay["enums"].get(f"{ed.package}.{ed.name}")
        out = emit_enum_yaml(ed, group=g, overlay=ov)
        with open(os.path.join(OUT_ENUM, f"{ed.package}.{ed.name}.yaml"), "w") as f:
            f.write(out)
        written_enum += 1

    with open(os.path.join(OUT_GEN, "file_index.json"), "w") as f:
        json.dump(file_index, f, indent=2, ensure_ascii=False)

    with open(os.path.join(OUT_GEN, "external_refs.yaml"), "w") as f:
        f.write("# External types referenced by car_hire protos (defined in shared protos).\n")
        f.write("# Modelled subset is controlled by scope.yaml.\n\n")
        for t in sorted(ext_refs):
            f.write(f"{t}:\n")
            f.write(f"  used_by_count: {len(ext_refs[t])}\n")
            for u in sorted(set(ext_refs[t]))[:8]:
                f.write(f"  - {u}\n")
            f.write("\n")

    print(f"messages written: {written_msg}  by group: {group_counts}")
    print(f"enums written:    {written_enum}")
    print(f"external types:   {len(ext_refs)}")
    print(f"scope: {'scope.yaml' if scope else 'none (all protos full)'}")
    print(f"overlay: {len(overlay['messages'])} message + {len(overlay['enums'])} enum descriptions curated")


if __name__ == "__main__":
    main()
