let maxSoFar = nums[0];
let meanSoFar = nums[0];
let result = nums[0];

for (let i = 1; i < nums.length; i++) {
    let currentNum = nums[i];
    let currentMax = Math.max(currentNum * maxSoFar, currentNum * meanSoFar, currentNum);
    let currentMean = Math.min(currentNum * maxSoFar, currentNum * meanSoFar, currentNum);

    maxSoFar = currentMax;
    meanSoFar = currentMean;
    result = Math.max(result, maxSoFar);
}

return result;