// Initialize variables
let answer = Number.MIN_SAFE_INTEGER;
let currentProduct = 1;

// First Pass
for (let i = 0; i < nums.length; i++) {
    currentProduct *= nums[i];
    answer = Math.max(answer, currentProduct);
    if (currentProduct === 0) {
        currentProduct = 1;
    }
}

// Second Pass
currentProduct = 1;
for (let i = nums.length - 1; i >= 0; i--) {
    currentProduct *= nums[i];
    answer = Math.max(answer, currentProduct);
    if (currentProduct === 0) {
        currentProduct = 1;
    }
}

return answer;