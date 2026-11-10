#include <iostream>
#include <unordered_set>
using namespace std;

bool halvesAreAlike(string s) {
    unordered_set<char> vowels{'a', 'e', 'i', 'o', 'u', 'A', 'E', 'I', 'O', 'U'};
    int count = 0;
    int n = s.length();
    string a = s.substr(0, n/2);
    string b = s.substr(n/2, n/2);

    for (char c : a) {
        if (vowels.count(c)) count++;
    }

    for (char c : b) {
        if (vowels.count(c)) count--;
    }

    return count == 0;
}

int main() {
    string s = "book";
    if (halvesAreAlike(s)) {
        cout << "The halves are alike." << endl;
    } else {
        cout << "The halves are not alike." << endl;
    }

    return 0;
}