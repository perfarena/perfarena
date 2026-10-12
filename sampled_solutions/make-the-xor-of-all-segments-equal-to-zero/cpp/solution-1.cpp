// Sort the array in ascending order
sort(nums.begin(), nums.end());

// Calculate the maximum product difference
int maxPairProduct = nums[nums.size() - 1] * nums[nums.size() - 2];
int minPairProduct = nums[0] * nums[1];
int maxProductDifference = maxPairProduct - minPairProduct;

return maxProductDifference;