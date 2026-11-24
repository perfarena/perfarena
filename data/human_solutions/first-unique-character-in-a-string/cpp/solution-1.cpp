#include <iostream>
#include <unordered_map>
#include <string>

int firstUniqueCharIndex(std::string s) {
    std::unordered_map<char, int> basket;

    for (char c : s) {
        basket[c]++;
    }

    int answer = -1;
    bool flag = false;

    for (int i = 0; i < s.size(); i++) {
        if (basket[s[i]] == 1) {
            answer = i;
            flag = true;
            break;
        }
    }

    if (flag) {
        return answer;
    } else {
        return -1;
    }
}

int main() {
    std::string input = "leetcode";
    int result = firstUniqueCharIndex(input);
    std::cout << "Index of the first non-repeating character: " << result << std::endl;

    return 0;
}