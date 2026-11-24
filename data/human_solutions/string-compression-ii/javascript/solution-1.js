// Sort the input array by X values
points.sort((a, b) => a[0] - b[0]);

// Initialize variable to store the maximum width
let maxDiff = 0;

// Iterate through the array to find the maximum width
for (let i = 1; i < points.length; i++) {
    // Calculate the distance between adjacent points along the x-axis
    let diff = points[i][0] - points[i - 1][0];
    
    // Update the maximum width if the current width is greater
    maxDiff = Math.max(maxDiff, diff);
}

// Return the maximum width
return maxDiff;