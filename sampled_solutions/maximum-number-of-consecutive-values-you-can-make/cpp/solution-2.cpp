#include <iostream>
#include <unordered_map>
#include <unordered_set>
#include <utility>

int uniquePalindromicSubsequences(std::string s) {
    std::unordered_map<char, std::pair<int, int>> mp;
    int n = s.size();
    
    for (int i = 0; i < n; ++i) {
        char c = s[i];
        if (mp[c].first == -1) {
            mp[c].first = i;
        }
        mp[c].second = i;
    }
    
    int answer = 0;
    for (char c = 'A'; c <= 'Z'; ++c) {
        if (mp.find(c) != mp.end()) {
            std::unordered_set<char> st;
            for (int i = mp[c].first + 1; i < mp[c].second; ++i) {
                st.insert(s[i]);
            }
            answer += st.size();
        }
    }
    
    return answer;
}

int main() {
    std::string s = "hello hello hello hi guys good morning";
    int result = uniquePalindromicSubsequences(s);
    std::cout << "Number of unique characters in palindromic subsequences: " << result << std::endl;
    
    return 0;
}