#include <vector>
#include <algorithm>

int minDeletions(std::string s) {
    std::vector<int> freq(26, 0);
    for (char c : s) {
        freq[c - 'a']++;
    }

    std::sort(freq.begin(), freq.end(), std::greater<int>());

    int f = freq[0];
    int answer = 0;

    for (int i = 1; i < 26; i++) {
        if (freq[i] > f) {
            if (f > 0) {
                answer += freq[i] - f;
                f = f - 1;
            } else {
                answer += freq[i];
            }
        } else {
            f = std::min(f, freq[i] - 1);
        }
    }

    return answer;
}