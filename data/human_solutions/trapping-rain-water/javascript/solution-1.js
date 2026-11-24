// Initialize arrays to store left and right edges
const leftEdges = [];
const rightEdges = [];

// Calculate left edges
for (let i = 0; i < map.length; i++) {
    if (i === 0) {
        leftEdges[i] = map[i];
    } else {
        leftEdges[i] = Math.max(leftEdges[i - 1], map[i]);
    }
}

// Calculate right edges
for (let i = map.length - 1; i >= 0; i--) {
    if (i === map.length - 1) {
        rightEdges[i] = map[i];
    } else {
        rightEdges[i] = Math.max(rightEdges[i + 1], map[i]);
    }
}

// Calculate trapped water
let totalWater = 0;
for (let i = 0; i < map.length; i++) {
    const waterLevel = Math.min(leftEdges[i], rightEdges[i]) - map[i];
    if (waterLevel > 0) {
        totalWater += waterLevel;
    }
}

// Output the total trapped water
console.log("Total trapped water:", totalWater);