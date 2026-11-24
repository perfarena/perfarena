#include <iostream>
#include <vector>
#include <cmath>

int firstMissingPositive(std::vector<int>& nums) {
    int n = nums.size();
    for (int i = 0; i < n; ++i) {
        int index = std::abs(nums[i]) - 1;
        if (index < n) {
            nums[index] = nums[index] > 0 ? -nums[index] : nums[index];
        }
    }
    for (int i = 0; i < n; ++i) {
        if (nums[i] > 0) {
            return i + 1;
        }
    }
    return n + 1;
}

int main() {
    std::vector<int> numbers = {3, 4, -1, 1};
    int result = firstMissingPositive(numbers);
    std::cout << "The first missing positive number is: " << result << std::endl;
    return 0;
}