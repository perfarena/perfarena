// Sort the arrays num1 and num2
sort(num1.begin(), num1.end());
sort(num2.begin(), num2.end());

// Initialize an empty vector C to store the result
vector<int> C;

// Initialize two pointers i and j for num1 and num2 respectively
int i = 0, j = 0;

// Compare elements using two pointers
while (i < num1.size() && j < num2.size()) {
    if (num1[i] == num2[j]) {
        if (C.empty() || C.back() != num1[i]) {
            C.push_back(num1[i]);
        }
        i++;
        j++;
    } else if (num1[i] < num2[j]) {
        i++;
    } else {
        j++;
    }
}

// Return the vector C as the final result
return C;