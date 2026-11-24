#include <vector>

std::vector<int> calculateAbsoluteDifferences(const std::vector<int>& nums) {
    int totalSum = 0;
    for (int num : nums) {
        totalSum += num;
    }

    std::vector<int> result;
    int leftSum = 0;

    for (int i = 0; i < nums.size(); i++) {
        int valLeft = nums[i] * i - leftSum;
        int valRight = totalSum - leftSum - nums[i] * (nums.size() - i);
        leftSum += nums[i];
        result.push_back(valLeft + valRight);
    }

    return result;
}