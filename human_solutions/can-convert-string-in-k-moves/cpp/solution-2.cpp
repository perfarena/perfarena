#include <iostream>
#include <vector>
#include <unordered_map>

using namespace std;

int minDeletions(string s) {
    unordered_map<char, int> freq;
    for (char c : s) {
        freq[c]++;
    }

    vector<int> v(100001, 0); // Assuming maximum frequency is 100000
    int answer = 0;

    for (auto& it : freq) {
        while (v[it.second] > 0) {
            it.second--;
            answer++;
        }
        if (it.second > 0) {
            v[it.second] = 1;
        }
    }

    return answer;
}

int main() {
    string s = "aabbccc";
    cout << minDeletions(s) << endl; // Output: 2
    return 0;
}