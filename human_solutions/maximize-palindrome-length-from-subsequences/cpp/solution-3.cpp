#include <iostream>
#include <vector>
#include <string>

bool makeStringsEqual(std::vector<std::string>& words) {
    std::vector<int> count(26, 0); // Initialize count for each character

    for (const std::string& word : words) {
        for (char c : word) {
            count[c - 'a']++; // Count occurrences of each character
        }
    }

    for (int c : count) {
        if (c % words.size() != 0) {
            return false; // If counts are not divisible by the number of words, return false
        }
    }

    return true; // All characters can be distributed equally
}

int main() {
    std::vector<std::string> words = {"abc", "aba", "cab"};
    if (makeStringsEqual(words)) {
        std::cout << "All strings can be made equal." << std::endl;
    } else {
        std::cout << "Strings cannot be made equal." << std::endl;
    }

    return 0;
}