#include <iostream>
#include <vector>
#include <unordered_map>
#include <algorithm>

std::vector<int> topKFrequentElements(std::vector<int>& nums, int k) {
    std::unordered_map<int, int> frequencyMap;
    for (int num : nums) {
        frequencyMap[num]++;
    }

    std::vector<std::vector<int>> bucket(nums.size() + 1);
    for (auto& entry : frequencyMap) {
        bucket[entry.second].push_back(entry.first);
    }

    std::vector<int> result;
    for (int i = bucket.size() - 1; i >= 0 && result.size() < k; --i) {
        for (int num : bucket[i]) {
            result.push_back(num);
            if (result.size() == k) break;
        }
    }

    return result;
}

int main() {
    std::vector<int> nums = {1, 1, 1, 2, 2, 3};
    int k = 2;
    std::vector<int> result = topKFrequentElements(nums, k);

    std::cout << "Top " << k << " frequent elements are: ";
    for (int num : result) {
        std::cout << num << " ";
    }
    std::cout << std::endl;

    return 0;
}