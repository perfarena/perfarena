#include <iostream>
#include <vector>
#include <unordered_map>

bool redistributeCharacters(std::vector<std::string>& words) {
    int n = words.size();
    std::unordered_map<char, int> freqMap;

    for (const std::string& word : words) {
        for (char c : word) {
            freqMap[c]++;
        }
    }

    for (const auto& entry : freqMap) {
        if (entry.second % n != 0) {
            return false;
        }
    }

    return true;
}

int main() {
    std::vector<std::string> words = {"abc", "abc", "bc"};
    if (redistributeCharacters(words)) {
        std::cout << "All strings can be made equal." << std::endl;
    } else {
        std::cout << "Strings cannot be made equal." << std::endl;
    }

    return 0;
}