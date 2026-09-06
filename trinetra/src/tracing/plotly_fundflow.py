# src/tracing/plotly_fundflow.py
#
# A guaranteed-to-render fund flow graph using Plotly (already a project
# dependency) instead of an external vis-network.js CDN script. This
# avoids any issue where a blocked/slow external script silently
# prevents the graph from appearing inside Streamlit's iframe.

import plotly.graph_objects as go
from src.tracing.visualise_graph import assign_style_role, ROLE_STYLE
from src.tracing.peel_chain_analysis import detect_convergence_points


def build_layered_positions(root_node):
    """
    Assign each visible node an (x, y) position: x = hop depth,
    y = spread out vertically within that depth level. ALL levels are
    included -- victim, every intermediate hop, and the final hop --
    not just the last one.
    """
    positions = {}
    level_counts = {}

    def walk(node, depth=0, parent_id=None, is_root=False):
        role = assign_style_role(node, depth, is_root=is_root)
        if role is None:
            return

        level_counts.setdefault(depth, 0)
        y = level_counts[depth]
        level_counts[depth] += 1

        positions[node.address] = {'x': depth * 3, 'y': y, 'role': role, 'node': node,
                                    'parent': parent_id, 'depth': depth}

        for child in node.children:
            walk(child, depth=depth + 1, parent_id=node.address, is_root=False)

    walk(root_node, is_root=True)

    for depth, count in level_counts.items():
        addrs_at_level = [a for a, p in positions.items() if p['depth'] == depth]
        offset = (count - 1) / 2
        for i, addr in enumerate(addrs_at_level):
            positions[addr]['y'] = (i - offset) * 1.6

    return positions


def hex_to_rgba(hex_color, alpha):
    hex_color = hex_color.lstrip('#')
    r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def build_plotly_fundflow(root_node):
    """
    Build a card-style fund flow graph: each address is a rounded,
    color-coded card annotation showing its depth, role/category,
    short address, and amount -- connected by curved lines labeled
    with the transferred amount. Every hop level is shown, not just
    the final one. Convergence points (where multiple branches merge
    into one wallet) get a gold star overlay.
    """
    positions = build_layered_positions(root_node)

    if not positions:
        return None, []

    fig = go.Figure()

    # --- Curved (spline) connector lines, one trace per edge so each
    # gets its own smooth curve, with a midpoint hover marker showing
    # the full transaction detail (amount, hash, timestamp) ---
    for addr, pos in positions.items():
        node = pos['node']
        parent_addr = pos['parent']
        if not (parent_addr and parent_addr in positions and node.incoming_tx):
            continue

        parent_pos = positions[parent_addr]
        x0, y0 = parent_pos['x'], parent_pos['y']
        x1, y1 = pos['x'], pos['y']
        mid_x, mid_y = (x0 + x1) / 2, (y0 + y1) / 2

        fig.add_trace(go.Scatter(
            x=[x0, mid_x, x1], y=[y0, mid_y, y1],
            mode='lines', line=dict(width=2, color='#3a4a63', shape='spline'),
            hoverinfo='none', showlegend=False
        ))

        fig.add_trace(go.Scatter(
            x=[mid_x], y=[mid_y], mode='markers+text',
            marker=dict(size=10, color='rgba(0,0,0,0)'),
            text=[f"{node.incoming_tx['amount']:.2f}"],
            textposition='top center', textfont=dict(size=9, color='#7f93ad'),
            hovertext=[f"Amount: {node.incoming_tx['amount']:.2f} USDT<br>"
                       f"Hash: {node.incoming_tx['hash']}<br>"
                       f"Timestamp: {node.incoming_tx['timestamp']}"],
            hoverinfo='text', showlegend=False
        ))

    # --- Invisible marker per node (for hover + click target); the
    # visible "card" itself is drawn as an annotation below ---
    for role in ROLE_STYLE:
        role_addrs = [a for a, p in positions.items() if p['role'] == role]
        if not role_addrs:
            continue
        xs = [positions[a]['x'] for a in role_addrs]
        ys = [positions[a]['y'] for a in role_addrs]
        hover_texts = []
        for a in role_addrs:
            node = positions[a]['node']
            amount = node.incoming_tx['amount'] if node.incoming_tx else None
            tx_hash = node.incoming_tx['hash'] if node.incoming_tx else 'N/A'
            timestamp = node.incoming_tx['timestamp'] if node.incoming_tx else 'N/A'
            hover_texts.append(
                f"<b>{ROLE_STYLE[role]['label']}</b><br>Address: {a}<br>"
                f"Status: {node.status}<br>Reason: {node.reason}<br>"
                f"Confidence: {node.confidence if node.confidence else 'N/A'}<br>"
                f"Amount: {amount:.2f} USDT<br>" if amount else "Amount: N/A (start)<br>"
                f"Hash: {tx_hash}<br>Timestamp: {timestamp}"
            )
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode='markers', marker=dict(size=55, color='rgba(0,0,0,0)'),
            hovertext=hover_texts, hoverinfo='text', name=ROLE_STYLE[role]['label']
        ))

    # --- Card-style annotations (the visible boxes, matching the reference) ---
    annotations = []
    for addr, pos in positions.items():
        node = pos['node']
        style = ROLE_STYLE[pos['role']]
        amount = node.incoming_tx['amount'] if node.incoming_tx else None
        short_addr = f"{addr[:8]}...{addr[-6:]}"
        amount_line = f"{amount:.4f} USDT" if amount is not None else "starting point"

        card_text = (f"<b>DEPTH {pos['depth']}  ·  {style['label']}</b><br>"
                     f"{short_addr}<br>{amount_line}")

        annotations.append(dict(
            x=pos['x'], y=pos['y'], text=card_text, showarrow=False,
            align='left', font=dict(size=10, color='#e6edf3'),
            bgcolor=hex_to_rgba(style['color'], 0.15),
            bordercolor=style['color'], borderwidth=1.8, borderpad=6,
        ))

    # --- Convergence point overlay ---
    convergence_points = detect_convergence_points(root_node)
    convergence_addrs = [c['address'] for c in convergence_points if c['address'] in positions]
    if convergence_addrs:
        conv_x = [positions[a]['x'] for a in convergence_addrs]
        conv_y = [positions[a]['y'] - 0.55 for a in convergence_addrs]
        conv_hover = []
        for a in convergence_addrs:
            match = next(c for c in convergence_points if c['address'] == a)
            conv_hover.append(
                f"<b>⭐ CONVERGENCE POINT</b><br>Incoming paths: {match['number_of_incoming_paths']}<br>"
                f"Total converged: {match['total_converged_amount']} USDT<br>{match['significance']}"
            )
        fig.add_trace(go.Scatter(
            x=conv_x, y=conv_y, mode='markers', marker=dict(size=16, color='#f1c40f', symbol='star'),
            hovertext=conv_hover, hoverinfo='text', name='⭐ Convergence Point'
        ))

    fig.update_layout(
        annotations=annotations,
        showlegend=True,
        legend=dict(font=dict(color='white', size=10), bgcolor='rgba(0,0,0,0)',
                    orientation='h', yanchor='bottom', y=1.02),
        plot_bgcolor='#0a0e1a', paper_bgcolor='#0a0e1a',
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, fixedrange=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, fixedrange=False),
        height=650, margin=dict(l=30, r=30, t=50, b=30),
        hovermode='closest', dragmode='pan'
    )

    return fig, convergence_points