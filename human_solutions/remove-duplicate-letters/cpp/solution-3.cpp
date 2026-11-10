// Step 1: Count the frequencies of each character in the given string
unordered_map<char, int> frequency;
for (char c : s) {
    frequency[c]++;
}

// Step 2: Process the characters to remove duplicates and create the resulting string
vector<char> stack;
for (char c : s) {
    if (find(stack.begin(), stack.end(), c) != stack.end()) {
        while (!stack.empty() && stack.back() > c && frequency[stack.back()] > 1) {
            frequency[stack.back()]--;
            stack.pop_back();
        }
    } else {
        while (!stack.empty() && stack.back() > c && frequency[stack.back()] > 0) {
            frequency[stack.back()]--;
            stack.pop_back();
        }
        stack.push_back(c);
    }
}

// Step 3: Build the resulting string from the characters left in the stack
string answer;
for (char c : stack) {
    answer += c;
}

return answer;