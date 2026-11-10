#include <vector>
#include <string>

bool canRedistributeStrings(std::vector<std::string>& words) {
    std::vector<int> count(26, 0);
    int n = words.size();

    for (auto& word : words) {
        for (auto& c : word) {
            count[c - 'a']++;
        }
    }

    for (auto& c : count) {
        if (c != 0 && c % n != 0) {
            return false;
        }
    }

    return true;
}