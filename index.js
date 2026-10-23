const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + window.location.host + "/ws/stats");
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    document.getElementById("nodes").innerText = data.nodes;
    document.getElementById("power").innerText = data.power.toLocaleString();
    document.getElementById("jobs").innerText = data.jobs.toLocaleString();
};
