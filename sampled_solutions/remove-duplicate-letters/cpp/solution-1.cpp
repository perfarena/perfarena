// Initialize count and visited vectors
vector<int> count(26, 0);
vector<int> visited(26, 0);

// Count occurrences of each character
for (char c : s) {
    count[c - 'a']++;
}

string answer = "";
for (char c : s) {
    count[c - 'a']--;

    if (visited[c - 'a'] == 0) {
        while (!answer.empty() && answer.back() > c && count[answer.back() - 'a'] > 0) {
            visited[answer.back() - 'a'] = 0;
            answer.pop_back();
        }
        visited[c - 'a'] = 1;
        answer.push_back(c);
    }
}

return answer;