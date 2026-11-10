#include <vector>
#include <algorithm>

int minimizeMaxPairSum(std::vector<int>& nums) {
    std::sort(nums.begin(), nums.end());
    
    int n = nums.size();
    int i = 0;
    int maxSum = INT_MIN;

    while (i <= n / 2 - 1) {
        int sum = nums[i] + nums[n - i - 1];
        maxSum = std::max(maxSum, sum);
        i++;
    }

    return maxSum;
}