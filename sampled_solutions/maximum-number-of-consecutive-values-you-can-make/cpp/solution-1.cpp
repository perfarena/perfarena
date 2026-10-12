#include <iostream>
#include <unordered_map>
#include <unordered_set>
using namespace std;

int uniquePalindromicSubsequences(string s) {
    unordered_map<char, pair<int, int>> mp;
    for (char ch = 'a'; ch <= 'z'; ++ch) {
        mp[ch] = {-1, -1};
    }

    for (int i = 0; i < s.size(); ++i) {
        char ch = s[i];
        if (mp[ch].first == -1) {
            mp[ch].first = i;
        }
        mp[ch].second = i;
    }

    unordered_set<int> uniqueChars;
    for (auto it : mp) {
        char ch = s[it.first];
        int start = it.second.first;
        int end = it.second.second;
        for (int i = start + 1; i < end; ++i) {
            uniqueChars.insert(s[i]);
        }
    }

    int answer = 0;
    for (int ch : uniqueChars) {
        ++answer;
    }

    return answer;
}

int main() {
    string s = "aabca";
    cout << uniquePalindromicSubsequences(s) << endl;
    return 0;
}