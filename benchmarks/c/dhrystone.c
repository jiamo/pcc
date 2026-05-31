// Dhrystone 2.1 benchmark - simplified single-file version
// Original by Reinhold P. Weicker, 1984-1988
// Stresses: integer operations, string operations, procedure calls, assignments

#include <string.h>

typedef int Enumeration;
#define Ident_1 0
#define Ident_2 1
#define Ident_3 2
#define Ident_4 3
#define Ident_5 4

typedef int One_Thirty;
typedef int One_Fifty;
typedef char Capital_Letter;
typedef int Boolean;
typedef char Str_30[31];

struct Record {
    struct Record *Ptr_Comp;
    Enumeration Discr;
    union {
        struct {
            Enumeration Enum_Comp;
            int Int_Comp;
            char Str_Comp[31];
        } var_1;
        struct {
            Enumeration E_Comp_2;
            char Str_2_Comp[31];
        } var_2;
        struct {
            char Ch_1_Comp;
            char Ch_2_Comp;
        } var_3;
    } variant;
};

typedef struct Record Rec_Type;
typedef struct Record *Rec_Pointer;

// Global variables
static Rec_Type Rec_Pool[2];
static Rec_Pointer Ptr_Glob, Next_Ptr_Glob;
static int Int_Glob;
static Boolean Bool_Glob;
static char Ch_1_Glob, Ch_2_Glob;
static int Arr_1_Glob[50];
static int Arr_2_Glob[50][50];

static Enumeration Func_1(Capital_Letter Ch_1_Par, Capital_Letter Ch_2_Par) {
    Capital_Letter Ch_1_Loc, Ch_2_Loc;
    Ch_1_Loc = Ch_1_Par;
    Ch_2_Loc = Ch_1_Loc;
    if (Ch_2_Loc != Ch_2_Par)
        return Ident_1;
    else {
        Ch_1_Glob = Ch_1_Loc;
        return Ident_2;
    }
}

static Boolean Func_2(Str_30 Str_1_Par, Str_30 Str_2_Par) {
    One_Thirty Int_Loc;
    Capital_Letter Ch_Loc = 'A';
    Int_Loc = 2;
    while (Int_Loc <= 2) {
        if (Func_1(Str_1_Par[Int_Loc], Str_2_Par[Int_Loc + 1]) == Ident_1) {
            Ch_Loc = 'A';
            Int_Loc++;
        }
    }
    if (Ch_Loc >= 'W' && Ch_Loc < 'Z')
        Int_Loc = 7;
    if (Ch_Loc == 'R')
        return 1;
    else {
        if (strcmp(Str_1_Par, Str_2_Par) > 0) {
            Int_Loc += 7;
            Int_Glob = Int_Loc;
            return 1;
        } else
            return 0;
    }
}

static Boolean Func_3(Enumeration Enum_Par) {
    Enumeration Enum_Loc;
    Enum_Loc = Enum_Par;
    if (Enum_Loc == Ident_3) return 1;
    else return 0;
}

static void Proc_6(Enumeration Enum_Val, Enumeration *Enum_Ref) {
    *Enum_Ref = Enum_Val;
    if (!Func_3(Enum_Val)) *Enum_Ref = Ident_4;
    switch (Enum_Val) {
        case Ident_1: *Enum_Ref = Ident_1; break;
        case Ident_2: if (Int_Glob > 100) *Enum_Ref = Ident_1;
                      else *Enum_Ref = Ident_4; break;
        case Ident_3: *Enum_Ref = Ident_2; break;
        case Ident_4: break;
        case Ident_5: *Enum_Ref = Ident_3; break;
    }
}

static void Proc_7(One_Fifty Int_1_Par, One_Fifty Int_2_Par, One_Fifty *Int_Par) {
    One_Fifty Int_Loc;
    Int_Loc = Int_1_Par + 2;
    *Int_Par = Int_2_Par + Int_Loc;
}

static void Proc_8(int Arr_1_Par[], int Arr_2_Par[][50], int Int_1_Par, int Int_2_Par) {
    One_Fifty Int_Loc;
    Int_Loc = Int_1_Par + 5;
    Arr_1_Par[Int_Loc] = Int_2_Par;
    Arr_1_Par[Int_Loc + 1] = Arr_1_Par[Int_Loc];
    Arr_1_Par[Int_Loc + 30] = Int_Loc;
    int Int_Index;
    for (Int_Index = Int_Loc; Int_Index <= Int_Loc + 1; Int_Index++)
        Arr_2_Par[Int_Loc][Int_Index] = Int_Loc;
    Arr_2_Par[Int_Loc][Int_Loc - 1] += 1;
    Arr_2_Par[Int_Loc + 20][Int_Loc] = Arr_1_Par[Int_Loc];
    Int_Glob = 5;
}

static void Proc_1(Rec_Pointer Ptr_Val_Par);
static void Proc_3(Rec_Pointer *Ptr_Ref_Par);
static void Proc_4(void);
static void Proc_5(void);

static void Proc_1(Rec_Pointer Ptr_Val_Par) {
    Rec_Pointer Next_Record = Ptr_Val_Par->Ptr_Comp;
    *Ptr_Val_Par->Ptr_Comp = *Ptr_Glob;
    Ptr_Val_Par->variant.var_1.Int_Comp = 5;
    Next_Record->variant.var_1.Int_Comp = Ptr_Val_Par->variant.var_1.Int_Comp;
    Next_Record->Ptr_Comp = Ptr_Val_Par->Ptr_Comp;
    Proc_3(&Next_Record->Ptr_Comp);
    if (Next_Record->Discr == Ident_1) {
        Next_Record->variant.var_1.Int_Comp = 6;
        Enumeration tmp;
        Proc_6(Ptr_Val_Par->variant.var_1.Enum_Comp, &tmp);
        Next_Record->variant.var_1.Enum_Comp = tmp;
        Next_Record->Ptr_Comp = Ptr_Glob->Ptr_Comp;
        int tmp2;
        Proc_7(Next_Record->variant.var_1.Int_Comp, 10, &tmp2);
        Next_Record->variant.var_1.Int_Comp = tmp2;
    } else {
        *Ptr_Val_Par = *Ptr_Val_Par->Ptr_Comp;
    }
}

static void Proc_3(Rec_Pointer *Ptr_Ref_Par) {
    if (Ptr_Glob != 0)
        *Ptr_Ref_Par = Ptr_Glob->Ptr_Comp;
    Proc_7(10, Int_Glob, &Ptr_Glob->variant.var_1.Int_Comp);
}

static void Proc_4(void) {
    Boolean Bool_Loc;
    Bool_Loc = Ch_1_Glob == 'A';
    Bool_Glob = Bool_Loc | Bool_Glob;
    Ch_2_Glob = 'B';
}

static void Proc_5(void) {
    Ch_1_Glob = 'A';
    Bool_Glob = 0;
}

int main(void) {
    int Number_Of_Runs = 20000000;
    int Run_Index;
    int Int_1_Loc, Int_2_Loc, Int_3_Loc;
    char Ch_Index;
    Enumeration Enum_Loc;
    Str_30 Str_1_Loc, Str_2_Loc;

    Next_Ptr_Glob = &Rec_Pool[0];
    Ptr_Glob = &Rec_Pool[1];
    Ptr_Glob->Ptr_Comp = Next_Ptr_Glob;
    Ptr_Glob->Discr = Ident_1;
    Ptr_Glob->variant.var_1.Enum_Comp = Ident_3;
    Ptr_Glob->variant.var_1.Int_Comp = 40;
    strcpy(Ptr_Glob->variant.var_1.Str_Comp, "DHRYSTONE PROGRAM, SOME STRING");
    strcpy(Str_1_Loc, "DHRYSTONE PROGRAM, 1'ST STRING");

    Arr_2_Glob[8][7] = 10;

    for (Run_Index = 1; Run_Index <= Number_Of_Runs; Run_Index++) {
        Proc_5();
        Proc_4();

        Int_1_Loc = 2;
        Int_2_Loc = 3;
        strcpy(Str_2_Loc, "DHRYSTONE PROGRAM, 2'ND STRING");
        Enum_Loc = Ident_2;
        Bool_Glob = !Func_2(Str_1_Loc, Str_2_Loc);

        while (Int_1_Loc < Int_2_Loc) {
            Int_3_Loc = 5 * Int_1_Loc - Int_2_Loc;
            Proc_7(Int_1_Loc, Int_2_Loc, &Int_3_Loc);
            Int_1_Loc += 1;
        }

        Proc_8(Arr_1_Glob, Arr_2_Glob, Int_1_Loc, Int_3_Loc);
        Proc_1(Ptr_Glob);

        for (Ch_Index = 'A'; Ch_Index <= Ch_2_Glob; ++Ch_Index) {
            if (Enum_Loc == Func_1(Ch_Index, 'C')) {
                Proc_6(Ident_1, &Enum_Loc);
                strcpy(Str_2_Loc, "DHRYSTONE PROGRAM, 3'RD STRING");
                Int_2_Loc = Run_Index;
                Int_Glob = Run_Index;
            }
        }

        Int_2_Loc = Int_2_Loc * Int_1_Loc;
        Int_1_Loc = Int_2_Loc / Int_3_Loc;
        Int_2_Loc = 7 * (Int_2_Loc - Int_3_Loc) - Int_1_Loc;

        Proc_1(Ptr_Glob);
    }

    int result = Int_Glob + Bool_Glob + Ch_1_Glob + Ch_2_Glob +
                 Arr_1_Glob[8] + Arr_2_Glob[8][7] +
                 Ptr_Glob->variant.var_1.Int_Comp +
                 Next_Ptr_Glob->variant.var_1.Int_Comp;
    return (result & 0xFF);
}
