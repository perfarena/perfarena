#include <iostream>
#include <stack>
#include <vector>

int largestRectangleArea(std::vector<int>& heights) {
    std::stack<int> st;
    int maxArea = 0;
    int n = heights.size();

    for (int i = 0; i <= n; i++) {
        while (!st.empty() && (i == n || heights[i] < heights[st.top()])) {
            int h = heights[st.top()];
            st.pop();
            int w = st.empty() ? i : i - st.top() - 1;
            maxArea = std::max(maxArea, h * w);
        }
        st.push(i);
    }

    return maxArea;
}

int main() {
    std::vector<int> heights = {3, 1, 2, 4, 2, 5, 6, 2};
    int result = largestRectangleArea(heights);
    std::cout << "Largest Rectangle Area: " << result << std::endl;

    return 0;
}