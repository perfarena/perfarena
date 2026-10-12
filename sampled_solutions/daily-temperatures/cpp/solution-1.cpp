#include <vector>
#include <stack>

std::vector<int> dailyTemperatures(std::vector<int>& temperatures) {
    int n = temperatures.size();
    std::vector<int> result(n, 0);
    std::stack<int> st;

    for (int i = n - 1; i >= 0; i--) {
        while (!st.empty() && temperatures[i] >= temperatures[st.top()]) {
            st.pop();
        }
        if (!st.empty()) {
            result[i] = st.top() - i;
        }
        st.push(i);
    }

    return result;
}