#include <iostream>
#include <vector>
#include <stack>
using namespace std;

string removeDuplicateLetters(string s) {
    vector<int> freq(26, 0);
    vector<bool> seen(26, false);
    stack<char> st;

    for (char c : s) {
        freq[c - 'a']++;
    }

    for (char c : s) {
        freq[c - 'a']--;

        if (seen[c - 'a']) continue;

        while (!st.empty() && c < st.top() && freq[st.top() - 'a'] > 0) {
            seen[st.top() - 'a'] = false;
            st.pop();
        }

        st.push(c);
        seen[c - 'a'] = true;
    }

    string result = "";
    while (!st.empty()) {
        result = st.top() + result;
        st.pop();
    }

    return result;
}

int main() {
    string s = "bcabc";
    cout << removeDuplicateLetters(s) << endl;

    return 0;
}