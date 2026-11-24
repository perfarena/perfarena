function findHighestAltitude(gain) {
    let result = [0];
    for (let i = 0; i < gain.length; i++) {
        let temp = result[i] + gain[i];
        result.push(temp);
    }
    return Math.max(...result);
}

// Test the function
const altitudeGain = [-5, 1, -4, 0, 7, -7];
console.log(findHighestAltitude(altitudeGain)); // Output: 1