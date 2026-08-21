from kernel.words import Lex, parse

LEX = Lex(
    parse(
        "a an ant ante anted be bee been cat cats do dog dogs eat eats "
        "go gone it its no not note on one so son song to top tops up",
    ),
)

BRANCH = Lex(
    parse(
        "ba bad bade bag bags ban band bane bar bard bare bat bats "
        "be bed bee been bet bets bi bid big bike bin bit bite",
    ),
)

CHAINY = Lex(parse("ab ac ad ba bb bc ca xa"))

THIN = Lex(parse("a be bed"))

DEAD = Lex(parse("a i"))

ONE_SEED = Lex(parse("ant ba"))

PROBES = ("", "a", "an", "ant", "ante", "anted", "anz", "b", "z", "'")
