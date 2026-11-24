#include <iostream>
#include <unordered_map>
#include <climits>

int firstNonRepeatingIndex(std::string s) {
    std::unordered_map<char, int> charIndexMap;
    for (int i = 0; i < s.size(); i++) {
        if (charIndexMap.find(s[i]) == charIndexMap.end()) {
            charIndexMap[s[i]] = i;
        } else {
            charIndexMap[s[i]] = INT_MAX;
        }
    }

    int answer = INT_MAX;
    for (auto it : charIndexMap) {
        answer = std::min(answer, it.second);
    }

    if (answer == INT_MAX) {
        return -1;
    } else {
        return answer;
    }
}

int main() {
    std::string testString = "leetcode";
    int result = firstNonRepeatingIndex(testString);
    std::cout << "Index of the first non-repeating character: " << result << std::endl;
    return 0;
}