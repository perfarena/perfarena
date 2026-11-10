// Initialize variables and check base condition
vector<string> rows(numRows);
bool down = true;
if (numRows == 1) {
    return s;
}

// Traverse the string and populate rows vector
int i = 0, j = 0;
for (char c : s) {
    rows[j].push_back(c);
    if (down) {
        if (j == numRows - 1) {
            j--;
            down = false;
        } else {
            j++;
        }
    } else {
        if (j == 0) {
            j++;
            down = true;
        } else {
            j--;
        }
    }
}

// Concatenate rows to form the zigzag pattern output
string answer;
for (string row : rows) {
    answer += row;
}

return answer;