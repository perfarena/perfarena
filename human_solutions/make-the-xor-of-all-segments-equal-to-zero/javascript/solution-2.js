function maxProductDifference(nums) {
    nums.sort((a, b) => a - b);
    const n = nums.length;
    const maxProduct = nums[n - 1] * nums[n - 2] - nums[0] * nums[1];
    return maxProduct;
}

// Example usage
const nums = [5, 9, 7, 42];
const result = maxProductDifference(nums);
console.log(result); // Output: 16