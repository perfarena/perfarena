// Sort the array
nums.sort((a, b) => a - b);

// Calculate the product difference
const result = (nums[nums.length - 1] * nums[nums.length - 2]) - (nums[0] * nums[1]);

return result;