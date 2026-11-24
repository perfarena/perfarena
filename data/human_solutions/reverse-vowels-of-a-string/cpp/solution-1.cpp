#include <unordered_set>
#include <string>

std::string reverseVowels(std::string s) {
    std::unordered_set<char> myVowels{'a', 'e', 'i', 'o', 'u'};
    int left = 0, right = s.size() - 1;

    while (left < right) {
        while (left < right && myVowels.find(s[left]) == myVowels.end()) {
            left++;
        }
        while (left < right && myVowels.find(s[right]) == myVowels.end()) {
            right--;
        }

        if (left < right) {
            std::swap(s[left], s[right]);
            left++;
            right--;
        }
    }

    return s;
}