#!/bin/bash
set -e

# Configuration
VAULT_ROOT="/mnt/NAS/vault"
TARGET_DIR="${VAULT_ROOT}/hackrf-ros-graph"
PROJECT_NODE="hackrf-ros"
RELATIVE_PATH="hackrf-ros-graph"
INDEX_NAME="hackrf_ros"

echo "Ensuring target directory exists: ${TARGET_DIR}"
mkdir -p "${TARGET_DIR}"

# Clean existing generated content safely
if [ -d "${TARGET_DIR}" ]; then
    echo "Cleaning previous graph notes from vault..."
    # Delete only markdown files and the canvas in the target directory to avoid collateral damage
    find "${TARGET_DIR}" -maxdepth 1 -name "*.md" -type f -delete
    rm -f "${TARGET_DIR}/graph.canvas"
fi

echo "Generating Obsidian vault using graphify Python API..."

# Python script to load the existing graphify graph, organize sub-parents, and export to obsidian
cat << 'EXPORT_EOF' > /tmp/export_graphify_obsidian_hackrf.py
import json
import sys
import re
from pathlib import Path
import networkx as nx
from networkx.readwrite import json_graph
from graphify.export import to_obsidian

def safe_name(label: str) -> str:
    # REPLICATE GRAPHIFY INTERNAL LOGIC EXACTLY
    return re.sub(r'[\\/*?:"<>|#^[\]]', "", str(label).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")).strip() or "unnamed"

def main():
    target_dir = Path(sys.argv[1])
    project_node = sys.argv[2]
    index_name = sys.argv[3]
    relative_path = "hackrf-ros-graph"

    graph_path = Path("graphify-out/graph.json")
    if not graph_path.exists():
        print(f"Error: {graph_path} not found. Please run graphify build first.", file=sys.stderr)
        sys.exit(1)
        
    try:
        raw_data = json.loads(graph_path.read_text(encoding="utf-8"))
        try:
            G = json_graph.node_link_graph(raw_data, edges="links")
        except TypeError:
            G = json_graph.node_link_graph(raw_data)
            
        communities = {}
        for node, data in G.nodes(data=True):
            cid = data.get("community")
            if cid is not None:
                communities.setdefault(cid, []).append(node)
                
        labels = {}
        manifest_path = Path("graphify-out/manifest.json")
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            labels = {int(k): v for k, v in manifest.get("community_labels", {}).items()}
            
        node_filename = {}
        seen_names = {}
        for node_id, data in G.nodes(data=True):
            label = data.get("label", node_id)
            base = safe_name(label)
            if base in seen_names:
                seen_names[base] += 1
                node_filename[node_id] = f"{base}_{seen_names[base]}"
            else:
                seen_names[base] = 0
                node_filename[node_id] = base

        n_notes = to_obsidian(G, communities, str(target_dir), community_labels=labels)
        print(f"Exported raw markdown notes from Graphify.")
        
        for f in target_dir.glob("_COMMUNITY_*.md"):
            f.unlink()
            
        sub_parents = {}
        orphans = []
        for cid, nodes in communities.items():
            if len(nodes) > 1:
                sub_parents[cid] = labels.get(cid, f"Community {cid}")
            else:
                orphans.extend(nodes)
                
        assigned_nodes = set(n for c in communities.values() for n in c)
        for node in G.nodes():
            if node not in assigned_nodes:
                orphans.append(node)

        valid_community_names = {} 
        for cid, label in sub_parents.items():
            s_label = safe_name(label)
            valid_community_names[cid] = s_label
            comm_file = target_dir / f"{s_label}.md"
            with open(comm_file, "w", encoding="utf-8") as f:
                f.write(f"# {label}\n\n")
                f.write("## Nodes in this Community\n")
                for node_id in sorted(communities[cid], key=lambda x: str(G.nodes[x].get('label', x)).lower()):
                    f.write(f"- [[{node_filename[node_id]}]]\n")
                f.write(f"\n\n---\n**Part of:** [[{relative_path}/{index_name}]]\n")

        for node_id, data in G.nodes(data=True):
            fname = node_filename[node_id]
            node_file = target_dir / f"{fname}.md"
            if not node_file.exists():
                continue
                
            cid = data.get("community")
            if cid in valid_community_names:
                parent_link = valid_community_names[cid]
            else:
                parent_link = index_name
                
            content = node_file.read_text(encoding="utf-8")
            content = re.sub(r'\n\n---\n\*\*Part of:\*\*.*', '', content, flags=re.DOTALL)
            content = content.rstrip()
            
            content += f"\n\n---\n**Part of:** [[{relative_path}/{parent_link}]]\n#graph-exclude\n"
            node_file.write_text(content, encoding="utf-8")

        index_file = target_dir / f"{index_name}.md"
        with open(index_file, "w", encoding="utf-8") as f:
            f.write(f"# HackRF ROS Knowledge Graph\n\n")
            f.write(f"**Parent Project:** [[{project_node}]]\n\n")
            f.write("Welcome to the semantic graph for the HackRF ROS project. ")
            f.write("Nodes are clustered into functional communities.\n\n")
            
            f.write("## 🏛️ Subsystems & Communities\n")
            for cid in sorted(valid_community_names.keys()):
                f.write(f"- [[{valid_community_names[cid]}]]\n")
                
            f.write("\n## 🧩 Orphan Nodes (Direct Links)\n")
            f.write("These nodes are isolated or form single-node communities.\n\n")
            for node_id in sorted(orphans, key=lambda x: str(G.nodes[x].get('label', x)).lower()):
                f.write(f"- [[{node_filename[node_id]}]]\n")
                
        print(f"Hierarchy established. Root index: {index_name}.md")

    except Exception as e:
        print(f"Error during export: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
EXPORT_EOF

python3 /tmp/export_graphify_obsidian_hackrf.py "${TARGET_DIR}" "${PROJECT_NODE}" "${INDEX_NAME}"

echo "Sync complete. Knowledge graph regenerated from scratch."
