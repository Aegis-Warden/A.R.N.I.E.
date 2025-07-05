import streamlit as st
import networkx as nx
from pyvis.network import Network
import tempfile
import os
import asyncio
import aiohttp
from urllib.parse import urljoin, urlparse
import re

# Set wide page layout for full-width graph
st.set_page_config(layout="wide")

# ---- 1. CRAWLER WITH DEPTH TAGGING ----
async def fetch(session, url):
    try:
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200 and 'text/html' in resp.headers.get('content-type', ''):
                return await resp.text()
    except Exception:
        pass
    return ''

async def get_robots_txt(session, base_url):
    robots_url = urljoin(base_url, '/robots.txt')
    try:
        async with session.get(robots_url) as resp:
            if resp.status == 200:
                return await resp.text()
    except Exception:
        pass
    return ''

def is_allowed(robots_txt, url, base_url):
    disallows = []
    user_agent = False
    for line in robots_txt.splitlines():
        line = line.strip()
        if line.lower().startswith('user-agent:'):
            user_agent = '*' in line
        elif user_agent and line.lower().startswith('disallow:'):
            path = line.split(':', 1)[1].strip()
            if path:
                disallows.append(urljoin(base_url, path))
        elif line == '':
            user_agent = False
    return not any(url.startswith(rule) for rule in disallows)

def extract_links(base_url, html):
    links = set()
    for match in re.findall(r'href=["\'](.*?)["\']', html, re.I):
        abs_link = urljoin(base_url, match)
        if urlparse(abs_link).netloc == urlparse(base_url).netloc:
            links.add(abs_link.split('#')[0])
    return links

async def crawl(base_url, max_depth=3):
    visited = {}
    edges = []
    async with aiohttp.ClientSession() as session:
        robots_txt = await get_robots_txt(session, base_url)
        async def _crawl(url, depth):
            if url in visited or depth > max_depth:
                return
            if robots_txt and not is_allowed(robots_txt, url, base_url):
                return
            visited[url] = depth
            html = await fetch(session, url)
            links = extract_links(base_url, html)
            for link in links:
                edges.append((url, link))
            tasks = [_crawl(link, depth+1) for link in links if link not in visited]
            await asyncio.gather(*tasks)
        await _crawl(base_url, 1)
    return visited, edges

# ---- 2. UI ----
st.title("ARNIE OSINT Crawler & Analyst Dashboard (v3)")

# ---- FORM FOR PARAMS ----
with st.form("crawl_form"):
    url = st.text_input("Enter website URL to crawl (http or https)", value="")
    max_depth = st.slider("Max crawl depth", 1, 5, 3)
    color_option = st.selectbox("Color nodes by", ["Keyword", "Crawl Depth", "Node Degree"])
    highlight_keywords = st.text_input("Highlight nodes containing (comma separated, for Keyword color mode)", value="word, word,")
    submitted = st.form_submit_button("Start Crawl")

# Session state for crawl params
if "crawl_params" not in st.session_state:
    st.session_state["crawl_params"] = {}

if submitted:
    st.session_state["crawl_params"] = {
        "url": url,
        "max_depth": max_depth,
        "color_option": color_option,
        "highlight_keywords": highlight_keywords
    }
    st.session_state["crawled"] = False  # mark as not yet crawled

# CRAWL or LOAD
if st.session_state.get("crawl_params") and not st.session_state.get("crawled", False):
    params = st.session_state["crawl_params"]
    with st.spinner("Crawling... (can take a while for big sites)"):
        visited, edges = asyncio.run(crawl(params["url"], params["max_depth"]))
        st.session_state["visited"] = visited
        st.session_state["edges"] = edges
        st.session_state["crawled"] = True
    st.success(f"Crawled {len(visited)} pages, {len(edges)} edges.")

# LOAD EXISTING FILE
st.markdown("---")
st.markdown("Or load previous crawl for analysis:")
import pickle
uploaded = st.file_uploader("Upload your .pkl (visited, edges) file", type=["pkl"])
if uploaded:
    visited, edges = pickle.load(uploaded)
    st.session_state["visited"] = visited
    st.session_state["edges"] = edges
    st.session_state["crawled"] = True
    st.success(f"Loaded {len(visited)} nodes, {len(edges)} edges.")

# ---- VISUALIZATION ----
if st.session_state.get("crawled", False):
    params = st.session_state["crawl_params"]
    visited = st.session_state["visited"]
    edges = st.session_state["edges"]
    G = nx.DiGraph()
    for (src, dst) in edges:
        G.add_edge(src, dst)
    nx.set_node_attributes(G, {k: v for k, v in visited.items()}, 'depth')
    nx.set_node_attributes(G, {n: G.degree(n) for n in G.nodes}, 'degree')

    color_option = params["color_option"]
    highlight_keywords = params["highlight_keywords"]
    color_map = {"llc": "blue", "nevada": "green", "palms": "red"}
    highlight_set = set(k.strip().lower() for k in highlight_keywords.split(","))

    degrees = dict(G.degree())
    sorted_nodes = sorted(degrees, key=degrees.get, reverse=True)
    max_nodes = st.slider("How many nodes to show (main graph)", 10, min(1000, len(sorted_nodes)), 500)
    top_nodes = sorted_nodes[:max_nodes]
    H = G.subgraph(top_nodes)

    def get_color(node):
        if color_option == "Keyword":
            label = node.lower()
            for kw in highlight_set:
                if kw and kw in label:
                    return color_map.get(kw, "orange")
            return "lightgray"
        elif color_option == "Crawl Depth":
            d = H.nodes[node].get("depth", 0)
            import matplotlib
            cmap = matplotlib.cm.get_cmap("coolwarm")
            val = min(1.0, d / (params["max_depth"] or 1))
            rgb = tuple(int(x * 255) for x in cmap(val)[:3])
            return f'rgb{rgb}'
        elif color_option == "Node Degree":
            deg = H.nodes[node].get("degree", 1)
            import matplotlib
            cmap = matplotlib.cm.get_cmap("viridis")
            val = min(1.0, deg / (max(degrees.values()) or 1))
            rgb = tuple(int(x * 255) for x in cmap(val)[:3])
            return f'rgb{rgb}'
        return "lightgray"

    if "watchlist" not in st.session_state:
        st.session_state["watchlist"] = []

    # CONTROLS ABOVE THE GRAPH
    st.markdown("### Graph Controls")
    st.write("- Use the slider to limit node count for clarity.")
    st.write("- Select node coloring mode.")
    st.write("- Add nodes to Analyst List.")
    st.write("- Use Ego Subgraph below for details.")

    net = Network(height="1200px", width="100%", directed=True, notebook=False)
    for node in H.nodes():
        label = node
        degree = H.nodes[node].get("degree", 1)
        depth = H.nodes[node].get("depth", 0)
        title = f"<b>URL:</b> {label}<br><b>In-degree:</b> {H.in_degree(node)}<br><b>Out-degree:</b> {H.out_degree(node)}<br><b>Crawl Depth:</b> {depth}"
        net.add_node(node, label=label, color=get_color(node), title=title)
    net.add_edges(list(H.edges()))
    net.show_buttons()
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".html") as tmpfile:
        net.write_html(tmpfile.name)
        htmlpath = tmpfile.name
    with open(htmlpath, 'r', encoding='utf-8') as f:
        html_code = f.read()
    st.components.v1.html(html_code, height=1200, scrolling=False)
    os.unlink(htmlpath)

    # EGO SUBGRAPH (Focus view)
    st.subheader("Node Focus: Ego Subgraph View")
    focus_node = st.selectbox("Select node to focus on", options=sorted(H.nodes()))
    ego_depth = st.slider("Connections to show (ego depth)", 1, 2, 1)
    if st.button("Show Ego Subgraph"):
        with st.spinner("Building ego subgraph..."):
            ego = nx.ego_graph(H, focus_node, radius=ego_depth, center=True, undirected=False)
            ego_net = Network(height="600px", width="100%", directed=True, notebook=False)
            for node in ego.nodes():
                label = node
                degree = ego.nodes[node].get("degree", 1)
                depth = ego.nodes[node].get("depth", 0)
                title = f"<b>URL:</b> {label}<br><b>In-degree:</b> {ego.in_degree(node)}<br><b>Out-degree:</b> {ego.out_degree(node)}<br><b>Crawl Depth:</b> {depth}"
                ego_net.add_node(node, label=label, color=get_color(node), title=title)
            ego_net.add_edges(list(ego.edges()))
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".html") as egofile:
                ego_net.write_html(egofile.name)
                egopath = egofile.name
            with open(egopath, 'r', encoding='utf-8') as f:
                ego_html_code = f.read()
            st.components.v1.html(ego_html_code, height=600, scrolling=True)
            os.unlink(egopath)

    # ANALYST LIST (WATCHLIST)
    st.subheader("Build Analyst List (select node, add to list):")
    add_node = st.selectbox("Add node to Analyst List", options=sorted(H.nodes()), key="add_node")
    if st.button("Add to Analyst List"):
        if add_node not in st.session_state["watchlist"]:
            st.session_state["watchlist"].append(add_node)
            st.success(f"Added: {add_node}")

    st.subheader("Analyst List")
    st.write(st.session_state["watchlist"])

    if st.button("Export Analyst List"):
        st.download_button("Download List as CSV", data="\n".join(st.session_state["watchlist"]), file_name="analyst_list.csv")
