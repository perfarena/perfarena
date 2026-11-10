vector<int> F(258, 0);
int left = 0, right = 0;
int answer = 0;

while (right < s.size()) {
    F[s[right]]++;
    while (F[s[right]] > 1) {
        F[s[left]]--;
        left++;
    }
    answer = max(answer, right - left + 1);
    right++;
}

return answer;