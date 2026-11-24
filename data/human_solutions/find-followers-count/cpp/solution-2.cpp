#include <iostream>

int main() {
    std::vector<int> nums = {3, 5, 4, 2, 4, 6};
    std::cout << "Minimized Maximum Pair Sum: " << minimizeMaxPairSum(nums) << std::endl;

    nums = {3, 5, 2, 3};
    std::cout << "Minimized Maximum Pair Sum: " << minimizeMaxPairSum(nums) << std::endl;

    return 0;
}