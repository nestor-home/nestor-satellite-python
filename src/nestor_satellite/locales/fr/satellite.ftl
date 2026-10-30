## nestor-satellite : ce qu'un satellite dit aux gens pendant l'appairage et la connexion.

# Le code de l'appareil, à l'écran à côté du code lui-même.
device-code-shown = Saisissez ce code dans Nestor pour appairer cet appareil.
# Le code de l'appareil, dans le journal.
device-code-logged = Code d'appairage { $code } : saisissez-le dans Nestor pour appairer cet appareil.
# Le code de l'appareil, à voix haute : $digits sont lus un par un.
device-code-spoken = Mon code d'appairage est { $digits }. Je répète : { $digits }.
# Un appareil sans écran ni voix.
device-code-headless = Cet appareil n'a ni écran ni voix : pour l'appairer, ajoutez-le dans Nestor avec un code d'enrôlement et renseignez NESTOR_PAIRING_URI.
device-code-expired = Le code d'appairage a expiré : demande d'un nouveau code.

enrollment-refused = { $reason ->
    [expired] Ce code d'enrôlement a expiré.
    [used] Ce code d'enrôlement a déjà servi.
    [cancelled] Ce code d'enrôlement a été annulé.
   *[unknown] Ce code d'enrôlement n'est pas valide.
}
enrollment-invalid = Ce n'est pas un code d'enrôlement.
enrollment-misconfigured = { $source } n'est ni un URI d'appairage ni un code d'enrôlement.
enrollment-not-pending = Cet appareil était déjà appairé avec son code : le code d'enrôlement n'a pas servi.
enrollment-accepted = Code d'enrôlement accepté.

paired = Appairé avec Nestor.
pairing-rate-limited = Trop de tentatives : nouvel essai dans { $seconds } s.
pairing-busy = Trop d'appareils attendent d'être appairés : nouvel essai dans { $seconds } s.
protocol-unsupported = Nestor ne parle pas la version du protocole de cet appareil : mettez à jour l'appareil ou Nestor.
pairing-rejected = Nestor a rejeté la demande d'appairage ({ $detail }) : mettez à jour cet appareil.
pairing-not-found = Pas de service d'appairage Nestor à { $core } : vérifiez l'adresse et que Nestor est à jour.
no-core = L'adresse de Nestor manque : indiquez-la, ou un URI d'appairage (NESTOR_PAIRING_URI).

token-refused = Nestor ne connaît plus cet appareil : nouvel appairage.
link-failed = La liaison avec Nestor a échoué ({ $error }) : nouvel essai dans { $seconds } s.
