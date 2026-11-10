int countPalindromicSubsequences(string s) {
    int n = s.length();
    vector<int> first(26, INT_MAX), last(26, -1);

    for (int i = 0; i < n; i++) {
        int idx = s[i] - 'a';
        first[idx] = min(first[idx], i);
        last[idx] = max(last[idx], i);
    }

    int answer = 0;
    for (char c = 'a'; c <= 'z'; c++) {
        int start = first[c - 'a'], end = last[c - 'a'];
        unordered_set<char> uniqueChars;
        for (int i = start; i <= end; i++) {
            uniqueChars.insert(s[i]);
        }
        answer += uniqueChars.size();
    }

    return answer;
}