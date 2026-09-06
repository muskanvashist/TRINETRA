# src/tracing/visualise_graph.py
#
# THE SINGLE, CANONICAL GRAPH VISUALIZATION FILE FOR THE PROJECT.
# Used by tree_trace.py standalone runs AND the dashboard.
#
# Shows ONLY the addresses that meaningfully carried the traced funds
# forward (pruned/low-value side branches excluded), styled with
# glowing role-labeled nodes: Victim, Suspect, Relay, Exchange, Mixer.
#
# Features:
# - Hover a node -> it and its directly connected nodes/edges highlight,
#   everything else dims
# - Click a node -> a side detail panel shows full address, role, status,
#   reason, confidence, amount, transaction hash, timestamp
# - Average forward time across the whole chain is computed and shown

import os
import json
import webbrowser


ROLE_STYLE = {
    'Victim':      {'color': '#2ecc71', 'label': 'VICTIM (true sender)'},
    'Suspect':     {'color': '#e74c3c', 'label': 'SUSPECT'},
    'Relay':       {'color': '#00d9ff', 'label': 'RELAY'},
    'Bridge':      {'color': '#1abc9c', 'label': 'BRIDGE (last hop before cash-out)'},
    'Exchange':    {'color': '#f39c12', 'label': 'EXCHANGE'},
    'Mixer':       {'color': '#9b59b6', 'label': 'MIXER'},
    'Fan-Out':     {'color': '#e67e22', 'label': 'FAN-OUT'},
    'Peel Chain':  {'color': '#ff6b9d', 'label': 'PEEL-CHAIN'},
    'Smurfing':    {'color': '#8e44ad', 'label': 'SMURFING'},
    'Dead End':    {'color': '#7f8c8d', 'label': 'DEAD END'},
    'Monitored':   {'color': '#4a5568', 'label': 'MONITORED (low priority)'},
}


def assign_style_role(node, depth, is_root=False):
    """
    Maps a HopNode to the design's role naming.

    Every branch the tracer ever touched is shown -- including
    low-priority/pruned branches, now labeled 'Monitored' instead of
    being hidden entirely. Nothing that was part of the trace tree
    silently disappears from the graph anymore.
    """
    forced = getattr(node, 'forced_role', None)
    if forced:
        return forced

    if is_root:
        return 'Suspect'
    if node.status == 'terminus':
        return 'Exchange'

    pattern = getattr(node, 'pattern_label', None)
    if pattern == 'SUSPECTED_MIXER':
        return 'Mixer'
    if pattern == 'FAN_OUT_LAYERING':
        return 'Fan-Out'
    if pattern == 'PEEL_CHAIN':
        return 'Peel Chain'
    if pattern == 'SMURFING':
        return 'Smurfing'

    if node.status == 'dead_end':
        return 'Dead End'
    if node.status == 'followed':
        has_terminus_child = any(c.status == 'terminus' for c in node.children)
        return 'Bridge' if has_terminus_child else 'Relay'
    if node.status == 'pruned':
        return 'Monitored'   # shown now, not hidden -- low priority, still visible
    return None


def compute_average_chain_delay(root_node):
    """
    Average time (in seconds) between consecutive hops along the whole
    traced tree -- gives an at-a-glance sense of how fast the funds moved.
    """
    delays = []

    def walk(node, parent_time=None):
        if node.incoming_tx:
            if parent_time is not None:
                delay = node.incoming_tx['timestamp'] - parent_time
                if delay >= 0:
                    delays.append(delay)
            current_time = node.incoming_tx['timestamp']
        else:
            current_time = None
        for c in node.children:
            walk(c, parent_time=current_time)

    walk(root_node, parent_time=None)
    return round(sum(delays) / len(delays), 1) if delays else None


def build_neon_graph_data(root_node):
    """
    Walk the trace tree and produce nodes/edges data for the graph,
    filtering out pruned branches, and attaching full detail info to
    each node/edge for the click-to-inspect panel.
    """
    nodes = []
    edges = []
    seen = set()
    role_counts = {'Relay': 0, 'Exchange': 0}

    def walk(node, parent_id=None, depth=0, is_root=False):
        role = assign_style_role(node, depth, is_root=is_root)
        if role is None:
            return

        if node.address not in seen:
            seen.add(node.address)
            style = ROLE_STYLE[role]

            if role == 'Relay':
                role_counts[role] += 1
                display_label = f"{style['label']}-{role_counts[role]}"
            else:
                display_label = style['label']

            amount = node.incoming_tx['amount'] if node.incoming_tx else None
            tx_hash = node.incoming_tx['hash'] if node.incoming_tx else None
            timestamp = node.incoming_tx['timestamp'] if node.incoming_tx else None

            nodes.append({
                'id': node.address,
                'display_label': display_label,
                'color': style['color'],
                'role': role,
                'address': node.address,
                'status': node.status,
                'reason': node.reason or '',
                'confidence': node.confidence,
                'amount': amount,
                'tx_hash': tx_hash,
                'timestamp': timestamp,
            })

        if parent_id and node.incoming_tx:
            edges.append({
                'from': parent_id,
                'to': node.address,
                'amount': node.incoming_tx['amount'],
                'hash': node.incoming_tx['hash'],
                'timestamp': node.incoming_tx['timestamp'],
            })

        for child in node.children:
            walk(child, parent_id=node.address, depth=depth + 1, is_root=False)

    walk(root_node, is_root=True)
    avg_delay = compute_average_chain_delay(root_node)
    return nodes, edges, avg_delay


def build_html(nodes, edges, avg_delay=None):
    """Build the standalone, accessible HTML page with vis-network."""
    nodes_json = json.dumps(nodes)
    edges_json = json.dumps(edges)

    def safe_class_name(role):
        return role.lower().replace(' ', '-').replace('_', '-')

    legend_items = "".join(
        f'<span class="legend-item"><span class="dot" style="background:{s["color"]};'
        f'box-shadow:0 0 8px {s["color"]};"></span>{s["label"]}</span>'
        for role, s in ROLE_STYLE.items()
    )

    avg_delay_text = f"{avg_delay:.0f} sec" if avg_delay is not None else "N/A"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Trinetra Fund Flow Graph</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.2/vis-network.min.js"></script>
  <style>
    body {{
      background-color: #0a0e1a;
      font-family: 'Segoe UI', sans-serif;
      margin: 0;
      color: white;
    }}
    #legend {{
      padding: 14px 24px;
      border-bottom: 1px solid #1c2333;
      display: flex;
      gap: 24px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .legend-item {{
      display: flex; align-items: center; gap: 8px;
      font-size: 13px; letter-spacing: 1px; color: #cfd8e3;
    }}
    .dot {{
      width: 12px; height: 12px; border-radius: 50%; display: inline-block;
    }}
    #header {{
      padding: 14px 24px;
      font-size: 14px; letter-spacing: 1px; color: #00d9ff;
      border-bottom: 1px solid #1c2333;
      display: flex; justify-content: space-between; align-items: center;
    }}
    #avg-delay {{
      color: #7f93ad; font-size: 12px;
    }}
    #main-container {{
      display: flex;
    }}
    #network {{
      width: 72%;
      height: 620px;
      background-color: #0a0e1a;
    }}
    #detail-panel {{
      width: 28%;
      height: 620px;
      padding: 18px;
      box-sizing: border-box;
      border-left: 1px solid #1c2333;
      overflow-y: auto;
      font-size: 13px;
    }}
    #detail-panel h3 {{
      color: #00d9ff; font-size: 14px; margin-top: 0;
    }}
    .detail-row {{
      margin-bottom: 10px;
      padding-bottom: 8px;
      border-bottom: 1px solid #1c2333;
    }}
    .detail-label {{
      color: #7f93ad; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
    }}
    .detail-value {{
      color: #e6edf3; word-break: break-all; margin-top: 2px;
    }}
    .placeholder {{
      color: #556275; font-style: italic;
    }}
  </style>
</head>
<body>
  <div id="header">
    <span>&#9670; FUND FLOW GRAPH &mdash; INTERACTIVE</span>
    <span id="avg-delay">Avg. forward time along chain: {avg_delay_text}</span>
  </div>
  <div id="legend">{legend_items}</div>

  <div id="main-container">
    <div id="network"></div>
    <div id="detail-panel">
      <h3>Address Details</h3>
      <p class="placeholder" id="placeholder-text">Click any node to see its full details here.</p>
      <div id="detail-content" style="display:none;"></div>
    </div>
  </div>

  <script>
    const nodesData = {nodes_json};
    const edgesData = {edges_json};

    const nodes = new vis.DataSet(nodesData.map(n => ({{
      id: n.id,
      label: n.display_label + (n.amount ? "\\n" + n.amount.toFixed(2) + " USDT" : ""),
      shape: 'circle',
      color: {{
        background: '#0a0e1a',
        border: n.color,
        highlight: {{ background: '#0a0e1a', border: n.color }}
      }},
      borderWidth: 2.5,
      font: {{ color: n.color, size: 11, multi: true, align: 'center' }},
      shadow: {{ enabled: true, color: n.color, size: 15, x: 0, y: 0 }},
      size: 32,
      _raw: n
    }})));

    const edges = new vis.DataSet(edgesData.map((e, idx) => ({{
      id: idx,
      from: e.from,
      to: e.to,
      label: e.amount.toFixed(2) + " USDT",
      dashes: true,
      color: {{ color: '#3a4a63', opacity: 0.8, highlight: '#00d9ff' }},
      font: {{ color: '#7f93ad', size: 10, strokeWidth: 0, background: '#0a0e1a' }},
      arrows: {{ to: {{ enabled: true, scaleFactor: 0.6 }} }},
      smooth: {{ type: 'cubicBezier', roundness: 0.4 }},
      _raw: e
    }})));

    const container = document.getElementById('network');
    const data = {{ nodes: nodes, edges: edges }};
    const options = {{
      layout: {{
        hierarchical: {{
          enabled: true,
          direction: 'LR',
          sortMethod: 'directed',
          levelSeparation: 190,
          nodeSpacing: 130
        }}
      }},
      physics: {{ enabled: false }},
      interaction: {{ hover: true, dragNodes: true, zoomView: true }}
    }};
    const network = new vis.Network(container, data, options);

    // ---------------- Hover highlighting ----------------
    function resetStyles() {{
      nodes.forEach(n => {{
        nodes.update({{ id: n.id, opacity: 1.0 }});
      }});
      edges.forEach(e => {{
        edges.update({{ id: e.id, color: {{ color: '#3a4a63', opacity: 0.8, highlight: '#00d9ff' }} }});
      }});
    }}

    network.on("hoverNode", function (params) {{
      const connectedNodeIds = network.getConnectedNodes(params.node);
      const connectedEdgeIds = network.getConnectedEdges(params.node);

      nodes.forEach(n => {{
        const dim = (n.id !== params.node && !connectedNodeIds.includes(n.id)) ? 0.15 : 1.0;
        nodes.update({{ id: n.id, opacity: dim }});
      }});
      edges.forEach(e => {{
        const dim = connectedEdgeIds.includes(e.id) ? 1.0 : 0.08;
        edges.update({{ id: e.id, color: {{ color: dim === 1.0 ? '#00d9ff' : '#3a4a63', opacity: dim }} }});
      }});
    }});

    network.on("blurNode", function () {{
      resetStyles();
    }});

    // ---------------- Click -> detail panel ----------------
    network.on("click", function (params) {{
      if (params.nodes.length > 0) {{
        const nodeId = params.nodes[0];
        const nodeData = nodes.get(nodeId)._raw;

        document.getElementById('placeholder-text').style.display = 'none';
        const panel = document.getElementById('detail-content');
        panel.style.display = 'block';

        const timeText = nodeData.timestamp
          ? new Date(nodeData.timestamp * 1000).toLocaleString()
          : 'N/A';

        panel.innerHTML = `
          <div class="detail-row">
            <div class="detail-label">Role</div>
            <div class="detail-value">${{nodeData.role}}</div>
          </div>
          <div class="detail-row">
            <div class="detail-label">Full Address</div>
            <div class="detail-value">${{nodeData.address}}</div>
          </div>
          <div class="detail-row">
            <div class="detail-label">Status</div>
            <div class="detail-value">${{nodeData.status || 'N/A'}}</div>
          </div>
          <div class="detail-row">
            <div class="detail-label">Reason</div>
            <div class="detail-value">${{nodeData.reason || 'Starting point of the trace.'}}</div>
          </div>
          <div class="detail-row">
            <div class="detail-label">Confidence</div>
            <div class="detail-value">${{nodeData.confidence !== null ? nodeData.confidence : 'N/A'}}</div>
          </div>
          <div class="detail-row">
            <div class="detail-label">Amount Received</div>
            <div class="detail-value">${{nodeData.amount ? nodeData.amount.toFixed(2) + ' USDT' : 'N/A (starting point)'}}</div>
          </div>
          <div class="detail-row">
            <div class="detail-label">Transaction Hash</div>
            <div class="detail-value">${{nodeData.tx_hash || 'N/A'}}</div>
          </div>
          <div class="detail-row">
            <div class="detail-label">Timestamp (Last Seen)</div>
            <div class="detail-value">${{timeText}}</div>
          </div>
        `;
      }}
    }});
  </script>
</body>
</html>
"""
    return html


def save_neon_graph(root_node, output_path="data/processed/neon_fundflow.html", auto_open=True):
    """Build and save the graph as a standalone HTML file."""
    nodes, edges, avg_delay = build_neon_graph_data(root_node)
    html = build_html(nodes, edges, avg_delay)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    absolute_path = os.path.abspath(output_path)
    print(f"Fund flow graph saved to {absolute_path}")

    if auto_open:
        webbrowser.open(f"file://{absolute_path}")

    return absolute_path


if __name__ == "__main__":
    from src.tracing.tree_trace import run_full_trace

    suspect_address = "TWXLTtvZKonEmYA2NNLSw5goGooeWT7Vj9"
    tree = run_full_trace(suspect_address)
    save_neon_graph(tree)