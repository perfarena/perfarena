// Initialize a vector of strings to store characters row by row
vector<string> answerA(3);

// Iterate through the characters of the input string
int i = 0;
bool flag = false;
for (char c : S) {
    answerA[i] += c; // Store character in the current row

    // Update the row index based on the zigzag pattern
    if (i == 0 || i == 2) {
        flag = !flag; // Flip the flag for direction change
    }
    i += flag ? 1 : -1;

    // Check if the row index is at the boundaries
    if (i == 0 || i == 2) {
        flag = !flag; // Flip the flag for direction change
    }
}

// Concatenate the characters row by row to form the final string
string result;
for (const string& row : answerA) {
    result += row;
}

// Return the final string
return result;