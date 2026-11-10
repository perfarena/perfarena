int findLargestSubstring(string s) {
    int n = s.size();
    unordered_map<char, int> charIndexMap;
    int maxi = 0;

    for (int i = 0; i < n; i++) {
        if (charIndexMap.find(s[i]) != charIndexMap.end()) {
            maxi = max(maxi, i - charIndexMap[s[i]] + 1);
        } else {
            charIndexMap[s[i]] = i;
        }
    }

    if (maxi == 0) {
        return -1;
    } else {
        return maxi - 2;
    }
}